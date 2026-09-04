"""The guest server-sent-event stream.

No wake-up channel: ``notify_participant`` is a no-op for a guest key and
stays one. ``ChatSSEProvider.poll`` drains a participant's call mailbox on
every poll regardless of the dirty flag, precisely because signalling latency
must stay low - this stream does the same, one ``drain_events(guest_key(...))``
per cycle. Adding Redis Pub/Sub (or any other wake-up) for guests would only
shave the poll interval off delivery latency while doubling the delivery
paths to keep in sync; do not add one.

Formatting, the periodic-reconnect budget and the keepalive cadence are
``core.views.sse``'s, not a second dialect: ``_format_sse`` is imported
verbatim, and ``_MAX_CONNECTION_SECONDS``/keepalive below are the same
values that module uses. Two poll cadences, not one, mirroring the split
core itself has between its Pub/Sub timeout tick and its cache dirty-check
throttle: the mailbox (``drain_events``, and so every ``meeting_*``/``call_*``
event) is drained every ``_POLL_INTERVAL_SECONDS`` (1s) so call signalling
latency stays low, same reasoning as the no-wake-up-channel paragraph above.
``resolve_guest`` and the message query are the expensive, DB-hitting half
and only run every ``_GATE_INTERVAL_SECONDS`` (2s), matching core's own
content cadence. Forwarding a ``call_*`` event therefore trusts the
``admitted`` flag most recently set by a gate cycle, which can be up to 2s
stale - the same staleness core accepts for its own dirty-flag check, and
bounded the same way: a removal or an occurrence closing is never learned
this way, because those go through the mailbox drain (meeting_removed) or
the gate itself on its next tick, never through a cached flag alone. One
exception, forced rather than merely bounded: a ``meeting_admitted`` drained
this cycle forces the gate to run immediately (see ``just_admitted`` below),
because the mailbox drain that just found it is destructive - a ``call_*``
event fanned out in the same window would already be gone from the mailbox
by the time an unforced gate tick eventually set ``admitted``, dropped
rather than merely delayed.

The loop's exit conditions are the security-relevant part. Every gate cycle
re-resolves the guest through ``resolve_guest`` - the same gate every other
guest endpoint passes through - and closes the moment it fails, which is what
makes ending the meeting or the occurrence window elapsing terminate the
stream within one gate interval. Removing the guest is faster still: it is
carried by ``meeting_removed`` through the mailbox, so it lands within one
poll interval regardless of the gate cadence.

Dead-letter decision: ``refuse_guest`` and ``remove_guest`` enqueue
``meeting_refused``/``meeting_removed`` into the guest's mailbox, but
``resolve_guest`` rejects a non-ADMITTED guest, so a loop gated on
``resolve_guest`` alone could never drain them - and the same is true of the
*view*, which is why ``MeetingGuestStreamView`` gates on ``guest_for_token``,
not ``resolve_guest``, and why gating this generator's own mailbox drain on
``resolve_guest`` would reintroduce the same dead letter one layer down. This
stream drains the mailbox through ``guest_for_token`` (the WAITING-tolerant
lookup) BEFORE the gate check every cycle, emits any of the four
``meeting_*`` lifecycle events found there, and stops on the three terminal
ones (refused, removed, ended) - option (a) from the brief, chosen over dropping
the enqueues because a stream is exactly the transport those two events were
always meant for: a removed or refused guest learns why, instead of a
channel that silently goes quiet. The same mailbox-before-gate ordering is
also what lets a WAITING guest hold a stream open to learn they were
admitted (``meeting_admitted`` is not terminal): the gate still fences every
other kind of content - a WAITING guest's ``call_*``/``message`` mailbox
contents are never forwarded, only a cycle where ``resolve_guest`` itself
resolves the guest as ADMITTED forwards those (see ``admitted`` below).

``meeting_ended`` reaches a guest two ways, and never both in one stream.
``end_meeting`` enqueues it to each WAITING row it sweeps, because that
sweep is the only thing that would otherwise happen to them silently - the
gate rejects the swept row on the next cycle and the loop returns before any
yield, closing on a zero-byte 200. An ADMITTED row is never swept, so it
never carries the enqueued event; for it the same name is synthesized below,
mirroring ``MeetingGuestStateView``'s own ``reported_state == "ended"``
case: an ADMITTED guest whose gate check nonetheless fails (closed
occurrence, or the window elapsed) is reported as ended, then the stream
stops. Both spellings are terminal.

Resume across a reconnect: the 600s budget forces one on schedule, and
``core.views.sse`` handles it by reading ``Last-Event-Id`` - this stream does
the same. A message's own uuid is its event id (see the ``message`` yield
below), so a reconnecting client's ``Last-Event-Id`` names the last message
it actually saw; resolving it to that message's ``created_at`` and using that
as the floor for ``since`` closes the gap a bare "since I connected" cursor
would otherwise drop on every reconnect - and, on the very first connect,
between the guest's REST history fetch and the stream opening. The
occurrence floor (``occurrence_start``) still wins via ``max()``: a
``Last-Event-Id`` naming a pre-window message can only be clamped up to the
floor, never used to reach below it.
"""

import time

from django.utils import timezone

from workspace.common.uuids import parse_uuid_or_none
from workspace.core.views.sse import _format_sse

from ..models import MeetingGuest, Message
from ..serializers import GuestMessageSerializer
from .call_signaling import drain_events
from .guest_messages import message_queryset
from .meeting_guests import guest_for_token, resolve_guest
from .participant_keys import guest_key

# Same budget as core.views.sse._MAX_CONNECTION_SECONDS: aligned with the
# nginx proxy-read-timeout: EventSource reconnects automatically.
_MAX_CONNECTION_SECONDS = 600
_KEEPALIVE_SECONDS = 15
_POLL_INTERVAL_SECONDS = 1
# core.views.sse._event_stream_polling's own dirty-check throttle.
_GATE_INTERVAL_SECONDS = 2
_MESSAGE_BATCH_LIMIT = 50

_LIFECYCLE_EVENTS = {
    "meeting_admitted",
    "meeting_refused",
    "meeting_removed",
    "meeting_ended",
}
_TERMINAL_LIFECYCLE_EVENTS = {
    "meeting_refused",
    "meeting_removed",
    "meeting_ended",
}


def _resolve_since(last_event_id, guest, fallback):
    """(since, uuid-to-preseed-into-seen) for the reconnect cursor.

    *fallback* is the stream's own connect instant, never "now" at the time
    the gate first passes - a guest who takes several gate cycles to be
    admitted must still see whatever was posted while they waited, not only
    what arrives after. The returned uuid, when not None, must be added to
    the caller's seen-message set: the message query below uses ``__gte`` on
    the floor (see its own comment), which would otherwise re-yield exactly
    the message the client is reconnecting from.
    """
    if not last_event_id:
        return fallback, None
    msg_uuid = parse_uuid_or_none(last_event_id)
    if msg_uuid is None:
        return fallback, None
    msg = (
        Message.objects.filter(
            uuid=msg_uuid, conversation_id=guest.meeting.conversation_id
        )
        .only("created_at")
        .first()
    )
    if msg is None:
        return fallback, None
    return msg.created_at, msg_uuid


def stream_guest_events(
    token, meeting_uuid, *, last_event_id=None, now=None, sleep=None
):
    """Yield formatted SSE strings for the guest named by *token*.

    *meeting_uuid* comes from the caller (``MeetingGuestStreamView``), which
    already paid for one ``guest_for_token`` + ``.meeting`` dereference to
    validate the slug before opening the stream - re-deriving it here every
    cycle would repeat that query for no reason, since a guest's meeting
    never changes across its lifetime. Comparing ``lobby_guest.meeting_id``
    (a plain column, already on the row ``guest_for_token`` returns) against
    it costs nothing extra.

    *now* and *sleep* default to ``None`` and are resolved to
    ``timezone.now``/``time.sleep`` inside the function body rather than as
    keyword defaults, so a test (or a caller, via patching the module-level
    ``time``/``timezone`` names) can override the real functions this
    generator's *default arguments* would otherwise have bound once, at
    import time, before any override could reach them.
    """
    now = now or timezone.now
    sleep = sleep or time.sleep

    start = now()
    since = None
    last_keepalive = start
    last_gate_check = None
    admitted = False
    # uuid -> created_at, pruned after each batch: since only grows, so an
    # entry whose created_at is already below it can never satisfy a future
    # __gte floor again - keeping it around would grow unboundedly over a
    # 600s connection for no reason.
    seen_message_ids = {}

    while True:
        current = now()
        if (current - start).total_seconds() > _MAX_CONNECTION_SECONDS:
            return

        lobby_guest = guest_for_token(token)
        if lobby_guest is None or lobby_guest.meeting_id != meeting_uuid:
            return

        drained = drain_events(guest_key(lobby_guest.uuid))
        lifecycle_events = [e for e in drained if e["event"] in _LIFECYCLE_EVENTS]
        call_events = [e for e in drained if e["event"].startswith("call_")]
        # Every event this codebase ever enqueues into a guest mailbox is one
        # of the four meeting_* lifecycle names or a call_* name (see
        # services/meetings.py and services/calls.py); anything else is
        # dropped here rather than forwarded to an audience it was never
        # meant for.

        stop = False
        just_admitted = False
        for envelope in lifecycle_events:
            yield _format_sse(envelope["event"], envelope["data"])
            if envelope["event"] in _TERMINAL_LIFECYCLE_EVENTS:
                stop = True
            if envelope["event"] == "meeting_admitted":
                just_admitted = True
        if stop:
            return

        # drain_events is destructive and runs every 1s cycle; admitted only
        # flips on a gate tick, up to _GATE_INTERVAL_SECONDS later. Without
        # forcing the gate here, a call_* event fanned out in that window
        # would already have been drained above (and so is gone) by the time
        # admitted turns True - dropped, not merely delayed.
        run_gate = (
            just_admitted
            or last_gate_check is None
            or (current - last_gate_check).total_seconds() >= _GATE_INTERVAL_SECONDS
        )
        if run_gate:
            last_gate_check = current
            guest = resolve_guest(token, now=current)
            admitted = guest is not None
            if guest is not None:
                if since is None:
                    since, resume_uuid = _resolve_since(last_event_id, guest, start)
                    if resume_uuid is not None:
                        seen_message_ids[resume_uuid] = since

                floor = max(since, guest.occurrence_start)
                new_messages = (
                    message_queryset()
                    .filter(
                        conversation_id=guest.meeting.conversation_id,
                        # >=, not >: a batch capped at _MESSAGE_BATCH_LIMIT
                        # can end on several messages sharing one timestamp,
                        # and a strict > would permanently skip whichever of
                        # those lands just past the cap next cycle.
                        # seen_message_ids is what keeps this from
                        # re-yielding ones already sent.
                        created_at__gte=floor,
                    )
                    .exclude(guest_id=guest.uuid)
                    .exclude(uuid__in=seen_message_ids)
                    .order_by("created_at")[:_MESSAGE_BATCH_LIMIT]
                )
                for msg in new_messages:
                    seen_message_ids[msg.uuid] = msg.created_at
                    since = max(since, msg.created_at)
                    serialized = GuestMessageSerializer(
                        msg, context={"floor": guest.occurrence_start}
                    ).data
                    yield _format_sse(
                        "message",
                        {"type": "message", "message": serialized},
                        str(msg.uuid),
                    )
                seen_message_ids = {
                    uuid: created
                    for uuid, created in seen_message_ids.items()
                    if created >= since
                }
            elif lobby_guest.state == MeetingGuest.State.ADMITTED:
                # ADMITTED per the DB row, yet resolve_guest just rejected
                # it: the occurrence closed or its window elapsed - report it
                # the way MeetingGuestStateView does, then stop.
                yield _format_sse("meeting_ended", {})
                return
            elif lobby_guest.state != MeetingGuest.State.WAITING:
                # REFUSED/REMOVED with nothing left to drain (already
                # delivered on an earlier cycle, or through a different
                # request entirely).
                return
            # else: WAITING and not yet admitted - keep the stream open.

        if admitted:
            for envelope in call_events:
                yield _format_sse(envelope["event"], envelope["data"])

        if (current - last_keepalive).total_seconds() >= _KEEPALIVE_SECONDS:
            yield ":keepalive\n\n"
            last_keepalive = current

        sleep(_POLL_INTERVAL_SECONDS)

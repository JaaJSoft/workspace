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
verbatim, and ``_MAX_CONNECTION_SECONDS``/keepalive/poll-interval below are
the same values that module uses.

The loop's exit conditions are the security-relevant part. Every cycle
re-resolves the guest through ``resolve_guest`` - the same gate every other
guest endpoint passes through - and closes the moment it fails, which is what
makes ending the meeting, removing the guest or the occurrence window
elapsing all terminate the stream within one poll interval.

Dead-letter decision: ``refuse_guest`` and ``remove_guest`` enqueue
``meeting_refused``/``meeting_removed`` into the guest's mailbox, but
``resolve_guest`` rejects a non-ADMITTED guest, so a loop gated on
``resolve_guest`` alone could never drain them. This stream drains the
mailbox through ``guest_for_token`` (the WAITING-tolerant lookup) BEFORE the
gate check every cycle, emits any of the four ``meeting_*`` lifecycle events
found there, and stops on the two terminal ones (refused, removed) - option
(a) from the brief, chosen over dropping the enqueues because a stream is
exactly the transport those two events were always meant for: a removed or
refused guest learns why, instead of a channel that silently goes quiet. The
same mailbox-before-gate ordering is also what lets a WAITING guest hold a
stream open to learn they were admitted (``meeting_admitted`` is not
terminal): the gate still fences every other kind of content - a WAITING
guest's ``call_*``/``message`` mailbox contents are never forwarded, only
resolved-and-ADMITTED cycles forward those.

``meeting_ended`` is never enqueued anywhere (ending a meeting sweeps only
WAITING rows, see ``end_meeting``); it is synthesized here, mirroring
``MeetingGuestStateView``'s own ``reported_state == "ended"`` case: an
ADMITTED guest whose gate check nonetheless fails (closed occurrence, or the
window elapsed) is reported as ended, then the stream stops.
"""

import time

from django.utils import timezone

from workspace.core.views.sse import _format_sse

from ..models import MeetingGuest, Message
from .call_signaling import drain_events
from .meeting_guests import guest_for_token, resolve_guest
from .participant_keys import guest_key

# Same budget as core.views.sse._MAX_CONNECTION_SECONDS: aligned with the
# nginx proxy-read-timeout: EventSource reconnects automatically.
_MAX_CONNECTION_SECONDS = 600
_KEEPALIVE_SECONDS = 15
_POLL_INTERVAL_SECONDS = 1
_MESSAGE_BATCH_LIMIT = 50

_LIFECYCLE_EVENTS = {"meeting_admitted", "meeting_refused", "meeting_removed"}
_TERMINAL_LIFECYCLE_EVENTS = {"meeting_refused", "meeting_removed"}


def stream_guest_events(token, slug, *, now=timezone.now, sleep=time.sleep):
    """Yield formatted SSE strings for the admitted guest named by *token*.

    *now* and *sleep* are injectable so the exit conditions (occurrence
    window elapsing, the connection budget) can be driven by a test without a
    real clock or a real wait.
    """
    start = now()
    since = start
    last_keepalive = start
    seen_message_ids = set()

    while True:
        current = now()
        if (current - start).total_seconds() > _MAX_CONNECTION_SECONDS:
            return

        lobby_guest = guest_for_token(token)
        if lobby_guest is None or lobby_guest.meeting.slug != slug:
            return

        drained = drain_events(guest_key(lobby_guest.uuid))
        lifecycle_events = [e for e in drained if e["event"] in _LIFECYCLE_EVENTS]
        call_events = [e for e in drained if e["event"].startswith("call_")]

        stop = False
        for envelope in lifecycle_events:
            yield _format_sse(envelope["event"], envelope["data"])
            if envelope["event"] in _TERMINAL_LIFECYCLE_EVENTS:
                stop = True
        if stop:
            return

        guest = resolve_guest(token, now=current)
        if guest is not None:
            for envelope in call_events:
                yield _format_sse(envelope["event"], envelope["data"])

            # Deferred to break the services -> views import cycle:
            # views.meeting_guest imports this module at load time, so this
            # module cannot import it back at load time. By the time this
            # generator is driven, views.meeting_guest is already fully
            # loaded, so this is safe.
            from ..views.meeting_guest import (
                _MESSAGE_SELECT_RELATED,
                _GuestMessageSerializer,
            )

            floor = max(since, guest.occurrence_start)
            new_messages = (
                Message.objects.filter(
                    conversation_id=guest.meeting.conversation_id,
                    created_at__gt=floor,
                )
                .exclude(guest_id=guest.uuid)
                .exclude(uuid__in=seen_message_ids)
                .select_related(*_MESSAGE_SELECT_RELATED)
                .order_by("created_at")[:_MESSAGE_BATCH_LIMIT]
            )
            for msg in new_messages:
                seen_message_ids.add(msg.uuid)
                since = max(since, msg.created_at)
                serialized = _GuestMessageSerializer(
                    msg, context={"floor": guest.occurrence_start}
                ).data
                yield _format_sse(
                    "message",
                    {"type": "message", "message": serialized},
                    str(msg.uuid),
                )
        elif lobby_guest.state == MeetingGuest.State.ADMITTED:
            # ADMITTED per the DB row, yet resolve_guest just rejected it: the
            # occurrence closed or its window elapsed - report it the way
            # MeetingGuestStateView does, then stop.
            yield _format_sse("meeting_ended", {})
            return
        elif lobby_guest.state != MeetingGuest.State.WAITING:
            # REFUSED/REMOVED with nothing left to drain (already delivered
            # on an earlier cycle, or through a different request entirely).
            return
        # else: WAITING and not yet admitted - keep the stream open.

        if (current - last_keepalive).total_seconds() >= _KEEPALIVE_SECONDS:
            yield ":keepalive\n\n"
            last_keepalive = current

        sleep(_POLL_INTERVAL_SECONDS)

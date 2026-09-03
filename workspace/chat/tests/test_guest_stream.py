"""The guest SSE stream: exit conditions and content gating.

Every generator is driven directly (never through a live HTTP connection),
with an injectable clock/sleep pair so a test controls exactly how many poll
cycles run and what wall-clock time each one observes - see ``_FakeClock``
and ``_drive`` below. ``GuestStreamViewTests`` covers the view's own gate,
which the generator-level tests cannot: they call ``stream_guest_events``
directly, bypassing ``MeetingGuestStreamView.get`` entirely.
"""

import re
from datetime import timedelta
from unittest.mock import patch

import orjson
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import MeetingGuest, Message
from workspace.chat.services import guest_stream
from workspace.chat.services.call_signaling import enqueue_event
from workspace.chat.services.meeting_guests import issue_token
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting, end_meeting, remove_guest
from workspace.chat.services.participant_keys import guest_key

User = get_user_model()


def make_event(owner, start=None, end=None):
    cal = Calendar.objects.create(name="Cal", owner=owner)
    return Event.objects.create(
        calendar=cal,
        owner=owner,
        title="Standup",
        start=start or timezone.now(),
        end=end,
    )


class _StopDriving(Exception):
    """Raised by a _FakeClock (or a patched sleep) to bound a test to a
    fixed number of poll cycles."""


class _FakeClock:
    """An injectable (now, sleep) pair for stream_guest_events.

    *advance* is added to the clock on every ``sleep()`` call, so a test can
    simulate the occurrence window elapsing or the connection budget being
    reached between one cycle and the next. *max_cycles* forces the driver to
    stop after that many completed cycles even if the stream never naturally
    terminates - it defaults to a generous-but-finite cap so a regression
    that breaks termination fails the test instead of hanging the suite; the
    WAITING-guest and message-gating tests pass an explicit small value since
    they know exactly how many cycles they need.
    """

    def __init__(self, start, advance=None, max_cycles=20):
        self.value = start
        self.advance = advance
        self.cycles = 0
        self.max_cycles = max_cycles

    def now(self):
        return self.value

    def sleep(self, _seconds):
        self.cycles += 1
        if self.advance is not None:
            self.value += self.advance
        if self.max_cycles is not None and self.cycles >= self.max_cycles:
            raise _StopDriving


def _drive(token, meeting_uuid, clock, last_event_id=None):
    """Run the generator to completion (or until the clock forces a stop).

    Returns (events, terminated) - terminated is True only when the
    generator itself returned (a real StopIteration), never when _drive gave
    up after max_cycles.
    """
    gen = guest_stream.stream_guest_events(
        token,
        meeting_uuid,
        last_event_id=last_event_id,
        now=clock.now,
        sleep=clock.sleep,
    )
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration:
        return events, True
    except _StopDriving:
        return events, False


def _parse(sse_text):
    """Decode one _format_sse() chunk back into (event_name, data)."""
    match = re.search(r"^data: (.+)$", sse_text, re.MULTILINE)
    payload = orjson.loads(match.group(1))
    return payload["event"], payload["data"]


def _event_id(sse_text):
    match = re.search(r"^id: (.+)$", sse_text, re.MULTILINE)
    return match.group(1) if match else None


class GuestStreamTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="host", password="x")
        self.now = timezone.now()
        self.event = make_event(
            self.owner,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.owner)
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]

    def tearDown(self):
        cache.clear()

    def _admit(self, display_name="Ada"):
        token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name=display_name,
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        return guest, token

    def _waiting(self, display_name="Waiting Wendy"):
        token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name=display_name,
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        return guest, token

    def _make_message(
        self, created_at, body="hi", author=None, guest=None, reply_to=None
    ):
        if author is None and guest is None:
            author = self.owner
        message = Message.objects.create(
            conversation=self.meeting.conversation,
            author=author,
            guest=guest,
            body=body,
            reply_to=reply_to,
            thread_root=reply_to,
        )
        Message.objects.filter(pk=message.pk).update(created_at=created_at)
        return message

    # --- exit conditions ---

    def test_closes_when_host_ends_meeting(self):
        _guest, token = self._admit()
        end_meeting(self.meeting, now=self.now)

        clock = _FakeClock(self.now)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertTrue(terminated)
        parsed = [_parse(e) for e in events]
        self.assertIn(("meeting_ended", {}), parsed)

    def test_closes_when_guest_removed(self):
        guest, token = self._admit()
        remove_guest(guest)

        clock = _FakeClock(self.now)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertTrue(terminated)
        parsed = [_parse(e) for e in events]
        self.assertIn("meeting_removed", [name for name, _data in parsed])

    @override_settings(MEETING_GRACE=timedelta(seconds=5))
    def test_closes_when_occurrence_window_elapses(self):
        # A short meeting so its window closes well inside the connection
        # budget (600s) - the default fixture's 30-minute grace would make
        # this indistinguishable from the connection-budget test below.
        short_event = make_event(
            self.owner, start=self.now, end=self.now + timedelta(minutes=1)
        )
        meeting = create_meeting(short_event, self.owner)
        occurrence_start = current_occurrence(meeting, now=self.now)[0]
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=occurrence_start,
            token_hash=token_hash,
        )

        clock = _FakeClock(self.now, advance=timedelta(minutes=2))
        events, terminated = _drive(token, meeting.uuid, clock)

        self.assertTrue(terminated)
        parsed = [_parse(e) for e in events]
        self.assertIn(("meeting_ended", {}), parsed)

    def test_closes_on_connection_budget(self):
        _guest, token = self._admit()

        clock = _FakeClock(self.now, advance=timedelta(seconds=700))
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertTrue(terminated)
        # Forced reconnect, not a gate failure: no meeting_ended is invented
        # for a guest who was never actually rejected by the gate.
        parsed = [_parse(e) for e in events]
        self.assertNotIn("meeting_ended", [name for name, _data in parsed])

    # --- WAITING guest: fenced content ---

    def test_waiting_guest_receives_meeting_admitted_and_nothing_else(self):
        guest, token = self._waiting()
        enqueue_event(
            guest_key(guest.uuid),
            "meeting_admitted",
            {"meeting_id": str(self.meeting.uuid)},
        )
        enqueue_event(guest_key(guest.uuid), "call_started", {"session_id": "whatever"})
        self._make_message(self.now + timedelta(seconds=1))

        clock = _FakeClock(self.now, max_cycles=1)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        self.assertEqual(
            parsed, [("meeting_admitted", {"meeting_id": str(self.meeting.uuid)})]
        )

    # --- call signalling ---

    def test_admitted_guest_receives_call_event_verbatim(self):
        guest, token = self._admit()
        enqueue_event(
            guest_key(guest.uuid),
            "call_signal",
            {"session_id": "abc", "signal": {"type": "offer"}},
        )

        clock = _FakeClock(self.now, max_cycles=1)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        self.assertIn(
            ("call_signal", {"session_id": "abc", "signal": {"type": "offer"}}), parsed
        )

    # --- message content ---

    def test_pre_floor_message_not_emitted_on_first_connect(self):
        _guest, token = self._admit()
        self._make_message(
            self.occurrence_start - timedelta(minutes=1), author=self.owner
        )

        clock = _FakeClock(self.now, max_cycles=1)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        self.assertNotIn("message", [name for name, _data in parsed])

    def test_in_window_message_is_emitted_redacted(self):
        _guest, token = self._admit()
        pre_window = self._make_message(
            self.occurrence_start - timedelta(minutes=1), author=self.owner
        )
        self._make_message(
            self.now + timedelta(seconds=1),
            author=self.owner,
            reply_to=pre_window,
        )

        clock = _FakeClock(self.now, max_cycles=1)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        message_events = [data for name, data in parsed if name == "message"]
        self.assertEqual(len(message_events), 1)
        envelope = message_events[0]
        self.assertNotIn("conversation_id", envelope)
        serialized = envelope["message"]
        self.assertNotIn("conversation_id", serialized)
        self.assertIsNone(serialized["reply_to"])

    def test_guests_own_message_is_not_echoed_back(self):
        guest, token = self._admit()
        self._make_message(self.now + timedelta(seconds=1), guest=guest)

        clock = _FakeClock(self.now, max_cycles=1)
        events, terminated = _drive(token, self.meeting.uuid, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        self.assertNotIn("message", [name for name, _data in parsed])

    # --- Last-Event-Id resume ---

    def test_message_posted_between_connections_is_delivered_on_reconnect(self):
        _guest, token = self._admit()
        first_msg = self._make_message(
            self.now + timedelta(seconds=1), author=self.owner
        )

        first_clock = _FakeClock(self.now, max_cycles=1)
        first_events, _terminated = _drive(token, self.meeting.uuid, first_clock)
        first_message_chunks = [e for e in first_events if _parse(e)[0] == "message"]
        self.assertEqual(len(first_message_chunks), 1)
        last_id = _event_id(first_message_chunks[0])
        self.assertEqual(last_id, str(first_msg.pk))

        second_msg = self._make_message(
            self.now + timedelta(seconds=2), author=self.owner
        )

        # The reconnect clock starts well after second_msg's created_at, so
        # the plain "since I connected" fallback would miss it entirely -
        # this is what proves the Last-Event-Id resume, not just its dedup,
        # is what recovers the gap message.
        second_clock = _FakeClock(self.now + timedelta(seconds=10), max_cycles=1)
        second_events, _terminated = _drive(
            token, self.meeting.uuid, second_clock, last_event_id=last_id
        )
        parsed = [_parse(e) for e in second_events]
        message_events = [data for name, data in parsed if name == "message"]
        # Exactly the message posted in the gap - not first_msg again.
        self.assertEqual(len(message_events), 1)
        self.assertEqual(message_events[0]["message"]["uuid"], str(second_msg.pk))

    def test_last_event_id_naming_a_pre_floor_message_does_not_lower_the_floor(self):
        _guest, token = self._admit()
        pre_floor = self._make_message(
            self.occurrence_start - timedelta(minutes=1), author=self.owner
        )
        in_window = self._make_message(
            self.now + timedelta(seconds=1), author=self.owner
        )

        clock = _FakeClock(self.now, max_cycles=1)
        events, _terminated = _drive(
            token, self.meeting.uuid, clock, last_event_id=str(pre_floor.pk)
        )
        parsed = [_parse(e) for e in events]
        message_events = [data for name, data in parsed if name == "message"]
        self.assertEqual(len(message_events), 1)
        self.assertEqual(message_events[0]["message"]["uuid"], str(in_window.pk))


class GuestStreamViewTests(TestCase):
    """Thin view-level checks - headers, the widened gate, and content
    fencing through the real StreamingHttpResponse, never draining more of
    the streaming body than a bounded, deterministic number of cycles (a
    patched ``sleep`` raises to stop pulling once that budget is spent, so a
    regression fails fast instead of hanging on a real 1s wait)."""

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="host", password="x")
        self.now = timezone.now()
        self.event = make_event(
            self.owner,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.owner)
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def _stream_url(self):
        return f"/api/v1/chat/meet/{self.meeting.slug}/stream"

    def test_invalid_token_is_404(self):
        resp = self.client.get(self._stream_url(), HTTP_X_MEETING_TOKEN="nope")
        self.assertEqual(resp.status_code, 404)

    def test_valid_token_opens_an_event_stream(self):
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        resp = self.client.get(self._stream_url(), HTTP_X_MEETING_TOKEN=token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.streaming)
        self.assertEqual(resp["Content-Type"], "text/event-stream")
        self.assertEqual(resp["Cache-Control"], "no-cache, no-transform")
        self.assertEqual(resp["X-Accel-Buffering"], "no")
        self.assertEqual(resp["Content-Encoding"], "identity")

    def test_waiting_guest_token_opens_a_stream(self):
        # The regression this pins: MeetingGuestStreamView used to gate on
        # resolve_guest, which rejects any non-ADMITTED row - a WAITING
        # guest's own token 404'd before the generator ever ran.
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Wendy",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        resp = self.client.get(self._stream_url(), HTTP_X_MEETING_TOKEN=token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.streaming)

    def test_waiting_guest_does_not_receive_call_events_until_admitted(self):
        token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Wendy",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        # A positive control alongside the thing under test: without it, this
        # test would also pass if drain_events returned nothing at all -
        # meeting_admitted proves events ARE flowing through this stream,
        # call_started proves that specific one is fenced regardless.
        enqueue_event(
            guest_key(guest.uuid),
            "meeting_admitted",
            {"meeting_id": str(self.meeting.uuid)},
        )
        enqueue_event(guest_key(guest.uuid), "call_started", {"session_id": "x"})

        resp = self.client.get(self._stream_url(), HTTP_X_MEETING_TOKEN=token)
        self.assertEqual(resp.status_code, 200)

        events = []

        def _stop_after_one_cycle(_seconds):
            raise _StopDriving

        # Patches the real time.sleep process-wide for the duration of this
        # block (guest_stream.time IS the stdlib time module, not a copy) -
        # safe here because nothing else in this synchronous test thread
        # calls it, and the context manager restores it unconditionally on
        # exit, including on the _StopDriving raise below.
        with patch(
            "workspace.chat.services.guest_stream.time.sleep",
            side_effect=_stop_after_one_cycle,
        ):
            content_iter = resp.streaming_content
            try:
                while True:
                    events.append(next(content_iter).decode("utf-8"))
            except StopIteration, _StopDriving:
                pass

        parsed = [_parse(e) for e in events]
        names = [name for name, _data in parsed]
        self.assertIn("meeting_admitted", names)
        self.assertNotIn("call_started", names)

    def test_stream_refuses_a_token_for_another_meeting(self):
        # The stream view moved off the shared _guest_for_request helper
        # onto its own inline slug check when it widened its gate off
        # resolve_guest - this pins that check at the layer the widening
        # made critical, with a real second meeting rather than a bare slug.
        other_owner = User.objects.create_user(username="other-host", password="x")
        other_event = make_event(
            other_owner,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        other_meeting = create_meeting(other_event, other_owner)
        other_occurrence_start = current_occurrence(other_meeting, now=self.now)[0]

        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=other_meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=other_occurrence_start,
            token_hash=token_hash,
        )

        resp = self.client.get(self._stream_url(), HTTP_X_MEETING_TOKEN=token)
        self.assertEqual(resp.status_code, 404)

    def test_waiting_guest_with_a_stale_occurrence_cannot_open_the_stream(self):
        # N1: guest_for_token does no occurrence check and stale WAITING rows
        # are never purged, so without this the documented per-occurrence
        # bound on concurrent streams would not hold - a token from any past
        # occurrence could hold a stream open forever, reconnected by
        # EventSource every 600s.
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Wendy",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start - timedelta(weeks=1),
            token_hash=token_hash,
        )
        resp = self.client.get(self._stream_url(), HTTP_X_MEETING_TOKEN=token)
        self.assertEqual(resp.status_code, 404)

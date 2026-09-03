"""The guest SSE stream: exit conditions and content gating.

Every generator is driven directly (never through a live HTTP connection),
with an injectable clock/sleep pair so a test controls exactly how many poll
cycles run and what wall-clock time each one observes - see ``_FakeClock``
and ``_drive`` below.
"""

import re
from datetime import timedelta

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
    """Raised by a _FakeClock to bound a test to a fixed number of poll cycles."""


class _FakeClock:
    """An injectable (now, sleep) pair for stream_guest_events.

    *advance* is added to the clock on every ``sleep()`` call, so a test can
    simulate the occurrence window elapsing or the connection budget being
    reached between one cycle and the next. *max_cycles*, when set, forces
    the driver to stop after that many completed cycles even if the stream
    never naturally terminates - the WAITING-guest and message-gating tests
    use this since the guest stays reachable indefinitely otherwise.
    """

    def __init__(self, start, advance=None, max_cycles=None):
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


def _drive(token, slug, clock):
    """Run the generator to completion (or until the clock forces a stop).

    Returns (events, terminated) - terminated is True only when the
    generator itself returned (a real StopIteration), never when _drive gave
    up after max_cycles.
    """
    gen = guest_stream.stream_guest_events(
        token, slug, now=clock.now, sleep=clock.sleep
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
        events, terminated = _drive(token, self.meeting.slug, clock)

        self.assertTrue(terminated)
        parsed = [_parse(e) for e in events]
        self.assertIn(("meeting_ended", {}), parsed)

    def test_closes_when_guest_removed(self):
        guest, token = self._admit()
        remove_guest(guest)

        clock = _FakeClock(self.now)
        events, terminated = _drive(token, self.meeting.slug, clock)

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
        events, terminated = _drive(token, meeting.slug, clock)

        self.assertTrue(terminated)
        parsed = [_parse(e) for e in events]
        self.assertIn(("meeting_ended", {}), parsed)

    def test_closes_on_connection_budget(self):
        _guest, token = self._admit()

        clock = _FakeClock(self.now, advance=timedelta(seconds=700))
        events, terminated = _drive(token, self.meeting.slug, clock)

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
        events, terminated = _drive(token, self.meeting.slug, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        self.assertEqual(
            parsed, [("meeting_admitted", {"meeting_id": str(self.meeting.uuid)})]
        )

    # --- message content ---

    def test_pre_floor_message_not_emitted_on_first_connect(self):
        _guest, token = self._admit()
        self._make_message(
            self.occurrence_start - timedelta(minutes=1), author=self.owner
        )

        clock = _FakeClock(self.now, max_cycles=1)
        events, terminated = _drive(token, self.meeting.slug, clock)

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
        events, terminated = _drive(token, self.meeting.slug, clock)

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
        events, terminated = _drive(token, self.meeting.slug, clock)

        self.assertFalse(terminated)
        parsed = [_parse(e) for e in events]
        self.assertNotIn("message", [name for name, _data in parsed])


class GuestStreamViewTests(TestCase):
    """Thin view-level checks - headers and the initial gate, never draining
    the streaming body (which would run the generator's real poll loop)."""

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

    def test_invalid_token_is_404(self):
        resp = self.client.get(
            f"/api/v1/chat/meet/{self.meeting.slug}/stream",
            HTTP_X_MEETING_TOKEN="nope",
        )
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
        resp = self.client.get(
            f"/api/v1/chat/meet/{self.meeting.slug}/stream",
            HTTP_X_MEETING_TOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.streaming)
        self.assertEqual(resp["Content-Type"], "text/event-stream")
        self.assertEqual(resp["Cache-Control"], "no-cache, no-transform")
        self.assertEqual(resp["X-Accel-Buffering"], "no")
        self.assertEqual(resp["Content-Encoding"], "identity")

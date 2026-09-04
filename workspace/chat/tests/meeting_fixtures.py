"""Shared fixtures and the SSE driving harness for the meeting-guest suites.

Four test modules build the same "meeting with a guest holding a token" world
(runtime, messages, stream, containment). They share these builders so they
cannot drift apart on what an admitted guest is, and so the containment audit
in ``test_guest_containment`` measures the same fixture the behavioural suites
assert on.

Not named ``test_*``: Django's discovery must ignore this module.
"""

import re

import orjson
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import MeetingGuest
from workspace.chat.services import guest_stream
from workspace.chat.services.meeting_guests import issue_token


def make_event(owner, start=None, end=None, title="Standup", calendar_name="Cal"):
    calendar = Calendar.objects.create(name=calendar_name, owner=owner)
    return Event.objects.create(
        calendar=calendar,
        owner=owner,
        title=title,
        start=start or timezone.now(),
        end=end,
    )


def guest_with_token(
    meeting,
    occurrence_start,
    *,
    display_name="Ada",
    state=MeetingGuest.State.ADMITTED,
):
    """A guest row plus the clear token, which only ``issue_token`` ever sees."""
    token, token_hash = issue_token()
    guest = MeetingGuest.objects.create(
        meeting=meeting,
        display_name=display_name,
        state=state,
        occurrence_start=occurrence_start,
        token_hash=token_hash,
    )
    return guest, token


class StopDriving(Exception):
    """Raised by a FakeClock (or a patched sleep) to bound a test to a fixed
    number of poll cycles."""


class FakeClock:
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
            raise StopDriving


def drive_guest_stream(token, meeting_uuid, clock, last_event_id=None):
    """Run the generator to completion (or until the clock forces a stop).

    Returns (events, terminated) - terminated is True only when the
    generator itself returned (a real StopIteration), never when the driver
    gave up after max_cycles.
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
    except StopDriving:
        return events, False


def parse_sse(sse_text):
    """Decode one format_sse() chunk back into (event_name, data)."""
    match = re.search(r"^data: (.+)$", sse_text, re.MULTILINE)
    payload = orjson.loads(match.group(1))
    return payload["event"], payload["data"]


def sse_event_id(sse_text):
    match = re.search(r"^id: (.+)$", sse_text, re.MULTILINE)
    return match.group(1) if match else None

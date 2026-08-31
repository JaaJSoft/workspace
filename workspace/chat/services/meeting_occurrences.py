"""Which occurrence of a meeting's event is reachable right now.

A meeting has one stable join URL for a whole recurring series, so validity is
not a pair of timestamps stored on the row - it is derived per request from the
event's recurrence. That is what makes a host ending today's standup close only
today's, and what makes the same link open again next week.

``occurrence_start`` and ``closed_occurrence_start`` may only ever be written
from this function's return value, never from ``event.start``: dateutil's
rrule truncates to whole seconds internally, so a recurring occurrence's start
never carries microseconds, and the non-recurring branch below truncates to
match. Writing ``event.start`` verbatim produces a value that this function's
own output will never equal again.
"""

from django.conf import settings
from django.utils import timezone

from workspace.calendar.recurrence import next_occurrences_after

# A reschedule can move an occurrence arbitrarily far from its virtual slot
# (checked separately, below, against every materialized exception), so the
# virtual-series scan can no longer stop at the first candidate the way it
# used to: skipping a cancelled slot means the loop keeps going. The scan
# needs an explicit bound instead. A run of this many consecutive
# cancellations hiding the next live occurrence is not a case worth
# optimizing for; it still terminates correctly, just returns None.
_OCCURRENCE_SCAN_LIMIT = 10


def _window(start, end):
    """The reachable span around an occurrence: lobby lead before, grace after."""
    return start - settings.MEETING_LOBBY_LEAD, end + settings.MEETING_GRACE


def _duration(event):
    if event.end is None:
        return settings.MEETING_DEFAULT_DURATION
    return event.end - event.start


def _exceptions_by_original_start(event):
    return {
        exc.original_start: exc
        for exc in event.exceptions.all()
        if exc.original_start is not None
    }


def current_occurrence(meeting, now=None):
    """Return ``(start, end)`` of the occurrence whose window contains *now*.

    Returns None when nothing is reachable, which is the common case between
    two occurrences of a series. A materialized exception (rescheduled or
    cancelled occurrence) is honoured the same way
    ``workspace.calendar.recurrence.expand_recurring_events`` honours it: a
    reschedule is checked at its own start/end, a cancellation is skipped.
    """
    now = now or timezone.now()
    event = meeting.event
    duration = _duration(event)

    if event.recurrence_frequency is None:
        start = event.start.replace(microsecond=0)
        end = start + duration
        opens_at, closes_at = _window(start, end)
        return (start, end) if opens_at <= now <= closes_at else None

    exceptions = _exceptions_by_original_start(event)

    # Check every materialized, non-cancelled exception against the window
    # directly: a reschedule can put it far from its original virtual slot,
    # outside the proximity the scan below assumes.
    for exc in exceptions.values():
        if exc.is_cancelled:
            continue
        start = exc.start
        end = exc.end if exc.end is not None else start + duration
        opens_at, closes_at = _window(start, end)
        if opens_at <= now <= closes_at:
            return start, end

    # Walk from far enough back that an occurrence already under way is still
    # yielded: its start can be up to one duration plus the grace behind us.
    floor = now - duration - settings.MEETING_GRACE
    occurrences = next_occurrences_after(event, floor, limit=_OCCURRENCE_SCAN_LIMIT)
    for occ_start in occurrences:
        if occ_start in exceptions:
            # Either cancelled, or a reschedule already checked (and
            # rejected) above - the virtual slot itself stays closed.
            continue
        end = occ_start + duration
        opens_at, closes_at = _window(occ_start, end)
        if opens_at <= now <= closes_at:
            return occ_start, end
    return None

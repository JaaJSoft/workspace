"""Which occurrence of a meeting's event is reachable right now.

A meeting has one stable join URL for a whole recurring series, so validity is
not a pair of timestamps stored on the row - it is derived per request from the
event's recurrence. That is what makes a host ending today's standup close only
today's, and what makes the same link open again next week.
"""

from django.conf import settings
from django.utils import timezone

from workspace.calendar.recurrence import next_occurrences_after


def _window(start, end):
    """The reachable span around an occurrence: lobby lead before, grace after."""
    return start - settings.MEETING_LOBBY_LEAD, end + settings.MEETING_GRACE


def _duration(event):
    if event.end is None:
        return settings.MEETING_DEFAULT_DURATION
    return event.end - event.start


def current_occurrence(meeting, now=None):
    """Return ``(start, end)`` of the occurrence whose window contains *now*.

    Returns None when nothing is reachable, which is the common case between
    two occurrences of a series.
    """
    now = now or timezone.now()
    event = meeting.event
    duration = _duration(event)

    if event.recurrence_frequency is None:
        start = event.start
        end = start + duration
        opens_at, closes_at = _window(start, end)
        return (start, end) if opens_at <= now <= closes_at else None

    # Walk from far enough back that an occurrence already under way is still
    # yielded: its start can be up to one duration plus the grace behind us.
    floor = now - duration - settings.MEETING_GRACE
    for start in next_occurrences_after(event, floor):
        end = start + duration
        opens_at, closes_at = _window(start, end)
        if now < opens_at:
            # Occurrences are chronological, so nothing later can contain now.
            return None
        if now <= closes_at:
            return start, end
    return None

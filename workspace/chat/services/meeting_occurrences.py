"""Which occurrence of a meeting's event is reachable right now.

A meeting has one stable join URL for a whole recurring series, so validity is
not a pair of timestamps stored on the row - it is derived per request from the
event's recurrence. That is what makes a host ending today's standup close only
today's, and what makes the same link open again next week.

``occurrence_start`` and ``closed_occurrence_start`` may only ever be written
from this function's return value, never from ``event.start``: dateutil's
rrule truncates to whole seconds internally, and every branch below - including
a materialized exception's own ``start``, which comes straight from a
DateTimeField and is never touched by rrule - truncates to match, so a value
this function returns never carries microseconds. Writing ``event.start``
verbatim produces a value that this function's own output will never equal
again.
"""

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from workspace.calendar.recurrence import next_occurrences_after

# A cancelled occurrence makes the virtual scan skip forward instead of
# stopping at the first candidate, which is what breaks the old termination
# proof ("occurrences are chronological, so nothing later can contain now").
# The scan needs an explicit bound instead. A run of this many consecutive
# cancellations hiding the next live occurrence is not a case worth
# optimizing for; it still terminates correctly, just returns None. A
# reschedule does not affect this scan at all - it is checked separately,
# against its own actual start/end, before the scan runs.
_OCCURRENCE_SCAN_LIMIT = 10


def _window(start, end):
    """The reachable span around an occurrence: lobby lead before, grace after."""
    return start - settings.MEETING_LOBBY_LEAD, end + settings.MEETING_GRACE


def _duration(event):
    if event.end is None:
        return settings.MEETING_DEFAULT_DURATION
    return event.end - event.start


def _reachable_exceptions(event, now, duration):
    """Materialized, non-cancelled exceptions whose own window could
    plausibly contain *now*.

    A reschedule can move an occurrence arbitrarily far from its original
    virtual slot, so this cannot be bounded by proximity to that slot the way
    ``_exception_original_starts`` below is. It is instead bounded by the same
    opens_at/closes_at formula the window check applies afterwards, evaluated
    against the exception's own start/end - a necessary condition for a
    match, so this can never exclude a true one. An exception with no end of
    its own falls back to *duration* (the meeting's master event duration),
    matching the fallback the caller applies afterwards.
    """
    return event.exceptions.filter(
        is_cancelled=False,
        # Filters on the raw start, while the match test above truncates it
        # first; truncated is always <= raw, so this can only exclude a
        # match by a sub-millisecond margin at the boundary - fail-closed,
        # not worth a query restructure for.
        start__lte=now + settings.MEETING_LOBBY_LEAD,
    ).filter(
        Q(end__isnull=False, end__gte=now - settings.MEETING_GRACE)
        | Q(end__isnull=True, start__gte=now - settings.MEETING_GRACE - duration)
    )


def _exception_original_starts(event, floor):
    """original_start values of exceptions (cancelled or rescheduled) whose
    virtual slot the scan below could actually yield.

    The scan never produces an occurrence start before *floor*, so nothing
    earlier is ever looked up against this set.
    """
    return set(
        event.exceptions.filter(original_start__gte=floor).values_list(
            "original_start", flat=True
        )
    )


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

    # is_recurring, not the rule text: apply_rule stores unparseable text
    # verbatim but derives is_recurring False, and the calendar expands such
    # an event as a one-off. Reading the same column keeps the two agreed.
    if not event.is_recurring:
        start = event.start.replace(microsecond=0)
        end = start + duration
        opens_at, closes_at = _window(start, end)
        return (start, end) if opens_at <= now <= closes_at else None

    # Check every plausibly-reachable, non-cancelled exception against the
    # window directly: a reschedule can put it far from its original virtual
    # slot, outside the proximity the scan below assumes.
    for exc in _reachable_exceptions(event, now, duration):
        start = exc.start.replace(microsecond=0)
        end = (
            exc.end.replace(microsecond=0) if exc.end is not None else start + duration
        )
        opens_at, closes_at = _window(start, end)
        if opens_at <= now <= closes_at:
            return start, end

    # Walk from far enough back that an occurrence already under way is still
    # yielded: its start can be up to one duration plus the grace behind us.
    floor = now - duration - settings.MEETING_GRACE
    exception_slots = _exception_original_starts(event, floor)
    occurrences = next_occurrences_after(event, floor, limit=_OCCURRENCE_SCAN_LIMIT)
    for occ_start in occurrences:
        if occ_start in exception_slots:
            # Either cancelled, or a reschedule already checked (and
            # rejected) above - the virtual slot itself stays closed.
            continue
        end = occ_start + duration
        opens_at, closes_at = _window(occ_start, end)
        if opens_at <= now <= closes_at:
            return occ_start, end
    return None

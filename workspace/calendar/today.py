"""The calendar's sources for the dashboard's Today card."""

from django.db.models import Q

from workspace.calendar.models import Event, EventMember
from workspace.calendar.recurrence import next_occurrences_after
from workspace.calendar.services.timezones import all_day_label
from workspace.calendar.upcoming import get_upcoming_for_user
from workspace.core.today_registry import TodayRows

INVITATIONS_LIMIT = 5
# Occurrences walked past cancelled ones before a series counts as over.
OCCURRENCE_LOOKAHEAD = 50


def todays_events(user, now, tz):
    """Every event still ahead today: the day is short enough to list whole."""
    events = get_upcoming_for_user(user, now, tz)
    return TodayRows(events, len(events))


def pending_invitations(user, now, tz):
    """Events the user was invited to and has not answered, soonest first.

    Only invitations that can still be answered usefully: a one-off event
    that is not over, a series that has an occurrence left. Each row
    carries ``next_start``, the occurrence the invitation is about now.
    """
    today_label = all_day_label(now.astimezone(tz).date())
    timed_not_over = Q(all_day=False) & (
        Q(end__gte=now) | Q(end__isnull=True, start__gte=now)
    )
    all_day_not_over = Q(all_day=True) & (
        Q(start__gte=today_label) | Q(end__gt=today_label)
    )
    one_off_ahead = Q(is_recurring=False) & (timed_not_over | all_day_not_over)
    series_running = Q(is_recurring=True) & (
        Q(recurrence_until__isnull=True)
        | Q(all_day=True, recurrence_until__gte=today_label)
        | Q(all_day=False, recurrence_until__gte=now)
    )
    events = (
        Event.objects.filter(
            members__user=user,
            members__status=EventMember.Status.PENDING,
            is_cancelled=False,
            recurrence_parent__isnull=True,
        )
        .filter(one_off_ahead | series_running)
        .select_related("calendar", "owner")
        .order_by("start")
    )
    events = list(events)
    exceptions = _exceptions_of([event for event in events if event.is_recurring])
    invitations = []
    for event in events:
        if event.is_recurring:
            event.next_start = _next_start(event, now, today_label, exceptions)
            if event.next_start is None:
                continue
        else:
            event.next_start = event.start
        invitations.append(event)
    invitations.sort(key=lambda event: event.next_start)
    return TodayRows(invitations[:INVITATIONS_LIMIT], len(invitations))


def _exceptions_of(series):
    """The materialized occurrences of *series*, by (series id, original start)."""
    if not series:
        return {}
    return {
        (exception.recurrence_parent_id, exception.original_start): exception
        for exception in Event.objects.filter(
            recurrence_parent_id__in=[event.uuid for event in series],
            original_start__isnull=False,
        ).only("recurrence_parent_id", "original_start", "start", "is_cancelled")
    }


def _next_start(series, now, today_label, exceptions):
    """Start of the first occurrence of *series* not over yet, or None.

    An occurrence under way counts. A cancelled one is skipped and a moved
    one answers with its new start, as ``expand_recurring_events`` does.
    """
    if series.all_day:
        after = today_label
    elif series.end is not None:
        after = now - (series.end - series.start)
    else:
        after = now
    for start in next_occurrences_after(series, after, OCCURRENCE_LOOKAHEAD):
        exception = exceptions.get((series.uuid, start))
        if exception is None:
            return start
        if not exception.is_cancelled:
            return exception.start
    return None

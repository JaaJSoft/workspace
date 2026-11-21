"""The calendar's sources for the dashboard's Today card."""

from django.db.models import Q

from workspace.calendar.models import Event, EventMember
from workspace.calendar.recurrence import next_occurrences_after
from workspace.calendar.services.timezones import all_day_label
from workspace.calendar.upcoming import get_upcoming_for_user
from workspace.core.today_registry import TodayRows

INVITATIONS_LIMIT = 5


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
        Q(recurrence_until__isnull=True) | Q(recurrence_until__gte=now)
    )
    events = (
        Event.objects.filter(
            members__user=user,
            members__status=EventMember.Status.PENDING,
            is_cancelled=False,
            recurrence_parent__isnull=True,
        )
        .filter(one_off_ahead | series_running)
        .select_related("calendar")
        .order_by("start")
    )
    invitations = []
    for event in events:
        if event.is_recurring:
            after = today_label if event.all_day else now
            event.next_start = next(next_occurrences_after(event, after, 1), None)
            if event.next_start is None:
                continue
        else:
            event.next_start = event.start
        invitations.append(event)
    invitations.sort(key=lambda event: event.next_start)
    return TodayRows(invitations[:INVITATIONS_LIMIT], len(invitations))

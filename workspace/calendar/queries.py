from django.db.models import Q

from .models import Calendar, CalendarSubscription, EventMember


def visible_calendar_ids(user):
    """Return calendar UUIDs the user can see: owned + subscribed."""
    owned = Calendar.objects.filter(owner=user).values_list('uuid', flat=True)
    subscribed = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
    return list(owned) + list(subscribed)


def visible_events_q(user):
    """Return a Q filter for events visible to the user.

    An event is visible if its calendar is owned/subscribed by the user,
    or if the user is a member of the event.
    """
    owned_cal_ids = Calendar.objects.filter(owner=user).values_list('uuid', flat=True)
    sub_cal_ids = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
    member_event_ids = EventMember.objects.filter(user=user).values_list('event_id', flat=True)
    return (
        Q(calendar_id__in=owned_cal_ids)
        | Q(calendar_id__in=sub_cal_ids)
        | Q(uuid__in=member_event_ids)
    )

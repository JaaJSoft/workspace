from django.db.models import Q

from .models import Calendar, CalendarSubscription, EventMember


def visible_calendar_ids(user):
    """Return calendar UUIDs the user can see: owned (incl. external) + subscribed.

    Single query: a disjunction over owner/subscriptions is cheaper than two
    round-trips and merging the lists in Python. ``.distinct()`` guards the
    edge case where a user subscribed to their own calendar (yields duplicate
    rows otherwise because of the JOIN).
    """
    return list(
        Calendar.objects
        .filter(Q(owner=user) | Q(subscriptions__user=user))
        .values_list('uuid', flat=True)
        .distinct()
    )


def visible_calendars(user):
    """Return (owned_qs, subscribed_qs) calendar querysets for *user*.

    Excludes external-source calendars from the owned set (those are
    managed separately via the external-calendars UI).

    Both querysets ``select_related('external_source')`` so the serializer's
    ``is_external`` check (``hasattr(obj, 'external_source')``) hits the row
    cache instead of issuing a lazy DB query per calendar. Owned rows are
    filtered on ``external_source__isnull=True`` (always None after the
    filter), but subscribed rows can include external calendars someone
    subscribed to — that's the branch that would otherwise N+1.
    """
    owned = (
        Calendar.objects
        .filter(owner=user, external_source__isnull=True)
        .select_related('owner', 'external_source')
    )
    sub_ids = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
    subscribed = (
        Calendar.objects
        .filter(uuid__in=sub_ids)
        .select_related('owner', 'external_source')
    )
    return owned, subscribed


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

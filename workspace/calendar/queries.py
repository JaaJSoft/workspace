from django.db.models import Q

from .models import Calendar, CalendarSubscription, EventMember


def visible_calendar_ids(user):
    """Return calendar UUIDs the user can see: owned (incl. external) + subscribed.

    Built as a UNION of two independently indexed queries rather than
    ``Q(owner=user) | Q(subscriptions__user=user)``: an OR whose branch
    crosses a join defeats per-branch index use and degrades to a scan of
    the whole calendar table, which grows with the user count. UNION also
    dedups the edge case where a user subscribed to their own calendar
    (the join form needed ``.distinct()`` for that). The empty
    ``order_by()`` is required on each branch - ``Calendar`` has a default
    ``Meta.ordering`` and ORDER BY is invalid inside a compound subquery.
    """
    owned = (
        Calendar.objects.filter(owner=user).order_by().values_list("uuid", flat=True)
    )
    subscribed = Calendar.objects.filter(subscriptions__user=user).order_by()
    return list(owned.union(subscribed.values_list("uuid", flat=True)))


def member_event_ids(user):
    """Return ids of events *user* is a member of (invited/added).

    Kept as its own helper so every consumer filters membership through the
    same subquery (``uuid__in=member_event_ids(user)``) instead of the
    ``members__user`` join - ORing that join with other conditions blocks
    index use (see ``visible_calendar_ids``) and fans out duplicate rows.
    """
    return EventMember.objects.filter(user=user).values_list("event_id", flat=True)


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
    owned = Calendar.objects.filter(
        owner=user, external_source__isnull=True
    ).select_related("owner", "external_source")
    sub_ids = CalendarSubscription.objects.filter(user=user).values_list(
        "calendar_id", flat=True
    )
    subscribed = Calendar.objects.filter(uuid__in=sub_ids).select_related(
        "owner", "external_source"
    )
    return owned, subscribed


def visible_events_q(user):
    """Return a Q filter for events visible to the user.

    An event is visible if its calendar is owned/subscribed by the user,
    or if the user is a member of the event.
    """
    owned_cal_ids = Calendar.objects.filter(owner=user).values_list("uuid", flat=True)
    sub_cal_ids = CalendarSubscription.objects.filter(user=user).values_list(
        "calendar_id", flat=True
    )
    return (
        Q(calendar_id__in=owned_cal_ids)
        | Q(calendar_id__in=sub_cal_ids)
        | Q(uuid__in=member_event_ids(user))
    )

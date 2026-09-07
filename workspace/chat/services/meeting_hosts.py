"""Who may run a meeting.

Hosts are derived, never stored: the event's owner and every invitee whose
RSVP is not declined for a scheduled meeting, the creator alone for an ad hoc
one. Deriving keeps the answer in step with the event, so inviting someone
after the meeting exists grants them the room and uninviting them takes it
away.
"""

from django.db.models import Q


def _host_q(user):
    from workspace.calendar.models import EventMember

    return (
        Q(event__owner=user)
        | Q(event__members__user=user)
        & ~Q(event__members__status=EventMember.Status.DECLINED)
        | Q(event__isnull=True, created_by=user)
    )


def host_ids(meeting):
    """User ids allowed to run *meeting*."""
    from workspace.calendar.models import EventMember

    if meeting.event_id is None:
        return {meeting.created_by_id}
    ids = {meeting.event.owner_id}
    ids.update(
        EventMember.objects.filter(event_id=meeting.event_id)
        .exclude(status=EventMember.Status.DECLINED)
        .values_list("user_id", flat=True)
    )
    return ids


def is_host(user, meeting):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.id in host_ids(meeting)


def hosted_meeting_ids(user):
    """Queryset of the uuids of every meeting *user* hosts, one query."""
    from ..models import Meeting

    if user is None or not getattr(user, "is_authenticated", False):
        return Meeting.objects.none().values_list("uuid", flat=True)
    return (
        Meeting.objects.filter(_host_q(user)).values_list("uuid", flat=True).distinct()
    )


def reachable_meeting(user, meeting_uuid):
    """The meeting *user* may run, or None (the caller answers 404 either way,
    so an unknown uuid and someone else's meeting look the same)."""
    from ..models import Meeting

    meeting = Meeting.objects.select_related("event").filter(uuid=meeting_uuid).first()
    if meeting is None or not is_host(user, meeting):
        return None
    return meeting

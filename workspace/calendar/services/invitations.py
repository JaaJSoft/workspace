"""Answering an event invitation.

Answering is not a local state flip: when the event came in as an ICS
invitation, the reply leaves the workspace as an iTIP REPLY mail to the
organiser. Both the REST view and the AI tool go through here so the mail
and the notification always follow the status write.
"""

from django.utils import translation
from django.utils.translation import gettext as _

from workspace.users.services.settings import get_user_language

from ..models import Event, EventMember


class NotInvitedError(Exception):
    """The user has no membership row on the event."""


def _response_title(status):
    """The organiser's notification title for an answer, in the active language."""
    if status == EventMember.Status.ACCEPTED:
        return _('%(user)s accepted "%(title)s"')
    if status == EventMember.Status.DECLINED:
        return _('%(user)s declined "%(title)s"')
    return _('%(user)s answered "%(title)s"')


def respond_to_invitation(event_id, user, status):
    """Record *user*'s answer to the invitation and fan out the consequences.

    Returns the ``(EventMember, Event)`` pair. Raises :class:`NotInvitedError`
    when the user was never invited.
    """
    from workspace.notifications.services.notifications import notify

    membership = EventMember.objects.filter(event_id=event_id, user=user).first()
    if not membership:
        raise NotInvitedError

    membership.status = status
    membership.save(update_fields=["status"])
    event = Event.objects.select_related("owner", "calendar").get(pk=event_id)

    if event.external_organizer and event.source_message_id:
        from workspace.calendar.tasks import send_ics_reply

        send_ics_reply.delay(str(event.pk), user.id, membership.status)

    if event.owner_id != user.id:
        with translation.override(get_user_language(event.owner)):
            notify(
                recipient=event.owner,
                origin="calendar",
                title=_response_title(membership.status)
                % {"user": user.username, "title": event.title},
                url=f"/calendar?event={event.pk}",
                actor=user,
                source=event,
            )
    return membership, event

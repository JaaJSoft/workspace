"""Emailed invitations (iTIP, RFC 5546) written into the invitation calendar.

Events keep their attendees and X- properties, but not the sender's alarms.
The METHOD of the message decides the write:

- ``REQUEST`` creates the object, or updates it when its SEQUENCE is newer.
  A REQUEST that only holds occurrences updates those occurrences.
- ``CANCEL`` cancels the object, or only the occurrences its RECURRENCE-IDs
  name.
- ``REPLY`` is an attendee's answer to an event the user organizes: it never
  creates an event, and is ignored until attendee answers are tracked.
- Every other method is ignored.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone as django_tz
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from workspace.calendar.models import Event, EventMember
from workspace.calendar.services.calendar_objects import save_object, save_occurrence
from workspace.calendar.services.event_creation import (
    get_or_create_invitation_calendar,
)
from workspace.calendar.services.ical_objects import (
    InvalidObject,
    dated,
    read_calendar,
    standalone,
    without_components,
)
from workspace.common.logging import scrub
from workspace.notifications.services.notifications import notify
from workspace.users.services.settings import get_user_language, get_user_timezone

logger = logging.getLogger(__name__)


def process_calendar_email(mail_message):
    """Process a single mail message containing a text/calendar attachment."""
    attachment = mail_message.attachments.filter(content_type="text/calendar").first()
    if not attachment:
        return

    user = mail_message.account.owner
    try:
        calendar = read_calendar(attachment.content.read(), get_user_timezone(user))
    except InvalidObject:
        logger.info("Ignored calendar email %s: not iCalendar", mail_message.pk)
        return

    # A message without METHOD is read as an invitation, which is what the
    # mailers that leave it out mean by attaching one.
    method = calendar.method or "REQUEST"
    handler = {"REQUEST": _handle_request, "CANCEL": _handle_cancel}.get(method)
    if handler is None:
        logger.info(
            "Ignored calendar email %s: method %s", mail_message.pk, scrub(method)
        )
        return
    for obj in calendar.objects:
        # Anyone can mail an invitation, and an alarm would put their reminder
        # on the user's devices (a sound fetched from their URL, an email to
        # addresses they chose): the organizer's alarms are not the attendee's,
        # which is how Thunderbird and Apple Calendar read invitations too.
        handler(without_components(obj, "VALARM"), mail_message)


def process_calendar_emails(messages):
    """Process multiple mail messages, catching and logging exceptions for each."""
    for message in messages:
        try:
            process_calendar_email(message)
        except Exception:
            logger.exception("Failed to process calendar email %s", message.pk)


def _organizer(obj):
    return (obj.master or obj.overrides[0]).organizer


def _invitation_events(uid, user, organizer):
    """The series masters of *user* created by the invitation *uid* from *organizer*.

    The UID alone proves nothing: it travels in every copy of the invitation
    and in the event payload, so a lookup on it would let any sender rewrite
    or cancel an event the user created, a row synced from an external feed,
    or another organizer's invitation. Feed rows are excluded by calendar,
    not by ``source``: rows synced before that column existed were backfilled
    as ICS. Several rows can match (one per calendar the invitation reached).
    """
    if not organizer:
        return []
    return list(
        Event.objects.filter(
            ical_uid=uid,
            owner=user,
            source=Event.Source.ICS,
            external_organizer__iexact=organizer,
            calendar__external_source__isnull=True,
            recurrence_parent__isnull=True,
        ).prefetch_related("exceptions")
    )


def _handle_request(obj, mail_message):
    """Create the invited object, or bring the one already there up to date."""
    user = mail_message.account.owner
    organizer = _organizer(obj)
    if not organizer:
        # iTIP requires an ORGANIZER on a REQUEST, and an event created
        # without one could never be matched by a later update or cancel.
        logger.info("Ignored invitation %s: no organizer", scrub(obj.uid))
        return

    existing = _invitation_events(obj.uid, user, organizer)
    if not existing:
        created = dated(standalone(obj))
        if created is None:
            logger.info("Ignored invitation %s: no start", scrub(obj.uid))
            return
        try:
            _create_event(created, organizer, mail_message)
        except IntegrityError:
            # The invitation calendar already holds this UID under another
            # organizer: the message is not an update of it.
            logger.info("Ignored invitation %s: UID already in use", scrub(obj.uid))
        return

    for master in existing:
        if obj.master is None and master.is_recurring:
            _update_occurrences(master, obj, mail_message)
            continue
        update = dated(standalone(obj))
        if update is not None and update.master.sequence > master.ical_sequence:
            _update_event(master, update, mail_message)


@transaction.atomic
def _create_event(obj, organizer, mail_message):
    user = mail_message.account.owner
    master = Event(
        calendar=get_or_create_invitation_calendar(mail_message.account),
        owner=user,
        ical_uid=obj.uid,
        external_organizer=organizer,
        source=Event.Source.ICS,
        source_message=mail_message,
    )
    save_object(master, obj)
    EventMember.objects.create(
        event=master, user=user, status=EventMember.Status.PENDING
    )
    _notify(master, _("Invitation: %(title)s"), _("From %(organizer)s"), organizer)


def _update_event(master, obj, mail_message):
    """Replace the series (or single event) and its exceptions by a newer REQUEST."""
    save_object(master, obj, source_message=mail_message)
    _notify(master, _("Updated: %(title)s"), _("The event has been updated"))


def _update_occurrences(master, obj, mail_message):
    """Write the occurrences a REQUEST updates, leaving the rest of the series.

    An occurrence is newer than what the user has when its SEQUENCE is above
    its exception's, or for an occurrence not yet changed, at least the
    series'.
    """
    current = {exc.original_start: exc for exc in master.exceptions.all()}
    updated = None
    for override in obj.overrides:
        if override.start is None:
            continue
        exc = current.get(override.recurrence_id)
        floor = master.ical_sequence - 1 if exc is None else exc.ical_sequence
        if override.sequence > floor:
            updated = save_occurrence(master, override, exc)
    if updated is not None:
        _notify(updated, _("Updated: %(title)s"), _("The event has been updated"))


def _handle_cancel(obj, mail_message):
    """Cancel the object, or only the occurrences the message names."""
    user = mail_message.account.owner
    for master in _invitation_events(obj.uid, user, _organizer(obj)):
        if obj.master is not None or not master.is_recurring:
            master.is_cancelled = True
            master.save(update_fields=["is_cancelled", "updated_at"])
            _notify(master, _("Cancelled: %(title)s"))
            continue
        current = {exc.original_start: exc for exc in master.exceptions.all()}
        for override in obj.overrides:
            exc = current.get(override.recurrence_id)
            if exc is None:
                exc = _occurrence_of(master, override.recurrence_id)
            exc.is_cancelled = True
            exc.ical_sequence = max(exc.ical_sequence, override.sequence)
            if exc._state.adding:
                exc.save()
            else:
                exc.save(update_fields=["is_cancelled", "ical_sequence", "updated_at"])
            _notify(exc, _("Cancelled: %(title)s"))


def _occurrence_of(master, original_start):
    """A new exception row for the unchanged occurrence of *master* at *original_start*."""
    duration = master.end - master.start if master.end else None
    return Event(
        calendar=master.calendar,
        owner=master.owner,
        recurrence_parent=master,
        original_start=original_start,
        source=master.source,
        title=master.title,
        description=master.description,
        location=master.location,
        start=original_start,
        end=original_start + duration if duration else None,
        all_day=master.all_day,
        timezone=master.timezone,
        ical_sequence=master.ical_sequence,
    )


def _notify(event, title, body=None, organizer=""):
    """Tell the owner about *event*, unless it is already over.

    *title* and *body* are lazy, so they are translated here, in the owner's
    language rather than the one of whoever triggered the mail sync.
    """
    if not _is_future_event(event):
        return
    with translation.override(get_user_language(event.owner)):
        payload = {
            "recipient": event.owner,
            "origin": "calendar",
            "title": title % {"title": event.title},
            "url": f"/calendar?event={event.pk}",
            "source": event,
        }
        if body is not None:
            payload["body"] = body % {"organizer": organizer}
        notify(**payload)


def _is_future_event(event):
    """Return True if the event ends (or starts) in the future."""
    ref = event.end or event.start
    if ref is None:
        return True
    return ref > django_tz.now()

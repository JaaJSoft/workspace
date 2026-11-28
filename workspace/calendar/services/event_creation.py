"""Creating events: the one a person writes, and the ones that come from a
mail - the invitation calendar of the account that received it, shared by
ICS invitations and LLM extraction, and the Event-row creation of the
extraction path.
"""

from django.db import transaction
from django.utils.translation import gettext as _

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.services import recurrence_rule
from workspace.calendar.services.guests import sync_guests
from workspace.calendar.services.notifications import notify_in_languages
from workspace.calendar.services.timezones import current_timezone_name
from workspace.users.queries import pickable_users


@transaction.atomic
def create_event(
    owner,
    *,
    calendar,
    title,
    start,
    end=None,
    all_day=False,
    description="",
    location="",
    rule="",
    member_ids=(),
    guests=(),
):
    """An event of *owner* in *calendar*, its invitees told about it.

    *member_ids* names accounts and *guests* people known by an email
    address. An id naming nobody who can be invited is left out.
    """
    event = Event(
        calendar=calendar,
        title=title,
        description=description,
        start=start,
        end=end,
        all_day=all_day,
        # All-day events are zone-less day labels; timed events anchor
        # their wall clock in the creator's active timezone.
        timezone="" if all_day else current_timezone_name(),
        location=location,
        owner=owner,
    )
    recurrence_rule.apply_rule(event, rule)
    event.save()

    invited = (
        list(pickable_users().filter(id__in=member_ids).exclude(id=owner.id))
        if member_ids
        else []
    )
    if invited:
        EventMember.objects.bulk_create(
            [EventMember(event=event, user=user) for user in invited]
        )
        notify_in_languages(
            recipients=invited,
            message=lambda: (
                _('Invited to "%(title)s"') % {"title": event.title},
                _("%(user)s invited you to an event.") % {"user": owner.username},
            ),
            url=f"/calendar?event={event.pk}",
            actor=owner,
            source=event,
        )
    if guests:
        sync_guests(event, guests)
    return event


def get_or_create_invitation_calendar(account):
    """Return the calendar that hosts mail-sourced events for `account`,
    creating it on first use."""
    calendar = Calendar.objects.filter(mail_account=account).first()
    if calendar:
        expected_name = account.display_name or account.email
        if calendar.name != expected_name:
            calendar.name = expected_name
            calendar.save(update_fields=["name", "updated_at"])
        return calendar

    return Calendar.objects.create(
        name=account.display_name or account.email,
        color="secondary",
        owner=account.owner,
        mail_account=account,
    )


@transaction.atomic
def create_event_from_payload(
    *,
    user,
    payload: dict,
    source_message,
    source: str = "",
    ical_uid: str = "",
    ical_sequence: int = 0,
    external_organizer: str = "",
) -> Event:
    """Create an Event from a normalised payload.

    payload keys: title (str, required), start (datetime, required,
    tz-aware), end (datetime or None), all_day (bool), location (str),
    description (str).

    source_message: required. The mail that produced this event. Its
    account determines which invitation calendar hosts the event.

    source: optional Event.Source value. Empty string leaves the model
    default (MANUAL). Callers set this to ICS or LLM as appropriate.
    """
    calendar = get_or_create_invitation_calendar(source_message.account)

    create_kwargs = dict(
        calendar=calendar,
        title=payload["title"],
        description=payload.get("description", ""),
        start=payload["start"],
        end=payload.get("end"),
        all_day=payload.get("all_day", False),
        timezone=payload.get("timezone", ""),
        location=payload.get("location", ""),
        owner=user,
        ical_uid=ical_uid or None,
        ical_sequence=ical_sequence,
        external_organizer=external_organizer or None,
        source_message=source_message,
    )
    if source:
        create_kwargs["source"] = source
    return Event.objects.create(**create_kwargs)

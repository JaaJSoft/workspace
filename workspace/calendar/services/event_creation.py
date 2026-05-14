"""Shared Event-creation primitives used by both ICS processing and
LLM extraction. Centralises the routing-to-invitation-calendar logic
and the Event-row creation so the two source paths stay in sync.
"""

from django.db import transaction

from workspace.calendar.models import Calendar, Event


def get_or_create_invitation_calendar(account):
    """Return the calendar that hosts mail-sourced events for `account`,
    creating it on first use. Kept verbatim from the previous ICS-only
    implementation so behaviour is unchanged."""
    calendar = Calendar.objects.filter(mail_account=account).first()
    if calendar:
        expected_name = account.display_name or account.email
        if calendar.name != expected_name:
            calendar.name = expected_name
            calendar.save(update_fields=['name', 'updated_at'])
        return calendar

    return Calendar.objects.create(
        name=account.display_name or account.email,
        color='secondary',
        owner=account.owner,
        mail_account=account,
    )


@transaction.atomic
def create_event_from_payload(
    *,
    user,
    payload: dict,
    source_message,
    source: str = '',
    ical_uid: str = '',
    ical_sequence: int = 0,
    external_organizer: str = '',
) -> Event:
    """Create an Event from a normalised payload.

    payload keys: title (str, required), start (datetime, required,
    tz-aware), end (datetime or None), all_day (bool), location (str),
    description (str).

    source_message: required. The mail that produced this event (ICS
    attachment OR LLM extraction). Its account determines which
    invitation calendar hosts the event.

    source: optional Event.Source value. Empty string leaves the model
    default (MANUAL). Callers set this to ICS or LLM as appropriate.
    """
    calendar = get_or_create_invitation_calendar(source_message.account)

    create_kwargs = dict(
        calendar=calendar,
        title=payload['title'],
        description=payload.get('description', ''),
        start=payload['start'],
        end=payload.get('end'),
        all_day=payload.get('all_day', False),
        location=payload.get('location', ''),
        owner=user,
        ical_uid=ical_uid or None,
        ical_sequence=ical_sequence,
        external_organizer=external_organizer or None,
        source_message=source_message,
    )
    if source:
        create_kwargs['source'] = source
    return Event.objects.create(**create_kwargs)

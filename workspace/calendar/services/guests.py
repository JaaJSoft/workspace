"""External guests: event attendees known by an email address, not an account."""

from django.db import transaction

from ..models import EventMember


def normalize_guests(guests):
    """Lowercase and dedupe ``[{email, name}]``, keeping the first name seen."""
    by_email = {}
    for guest in guests:
        email = (guest.get("email") or "").strip().lower()
        if email and email not in by_email:
            by_email[email] = (guest.get("name") or "").strip()
    return by_email


@transaction.atomic
def sync_guests(event, guests):
    """Make *event*'s external guests exactly *guests*.

    A guest already on the event keeps its row, and with it its status; only
    its display name follows the new value.
    """
    wanted = normalize_guests(guests)
    current = {m.email: m for m in event.members.filter(user__isnull=True)}

    stale = current.keys() - wanted.keys()
    if stale:
        EventMember.objects.filter(
            event=event, user__isnull=True, email__in=stale
        ).delete()

    renamed = []
    for email, name in wanted.items():
        member = current.get(email)
        if member is not None and member.name != name:
            member.name = name
            renamed.append(member)
    if renamed:
        EventMember.objects.bulk_update(renamed, ["name"])

    EventMember.objects.bulk_create(
        [
            EventMember(event=event, email=email, name=name)
            for email, name in wanted.items()
            if email not in current
        ]
    )


def copy_guests(source, target):
    """Give *target* the external guests of *source*, statuses included."""
    EventMember.objects.bulk_create(
        [
            EventMember(event=target, email=m.email, name=m.name, status=m.status)
            for m in source.members.filter(user__isnull=True)
        ]
    )

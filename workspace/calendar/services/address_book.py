"""Turn address-book persons and contact lists into event invitees."""

from dataclasses import dataclass, field

from django.db.models import Q

from workspace.people.queries import user_person_lists, user_persons


@dataclass
class ResolvedInvitees:
    users: list = field(default_factory=list)
    guests: list = field(default_factory=list)
    # Persons with neither an account nor an email: nothing to invite them by.
    skipped: list = field(default_factory=list)


def resolve_invitees(user, person_ids=(), list_ids=()):
    """Resolve *person_ids* and every member of *list_ids* for *user*.

    A person linked to an active workspace account resolves to that account,
    so the same human is never invited twice (once as a user, once by email).
    Any other person resolves to an external guest on their first email.
    Persons and lists *user* cannot reach are ignored, and *user* never
    resolves to themselves: the organizer is not their own guest.
    """
    reachable_lists = user_person_lists(user).filter(uuid__in=list_ids)
    persons = (
        user_persons(user)
        .filter(Q(uuid__in=person_ids) | Q(lists__in=reachable_lists))
        .select_related("linked_user")
        .distinct()
        .order_by("display_name", "uuid")
    )

    resolved = ResolvedInvitees()
    seen_user_ids = {user.id}
    seen_emails = set()
    for person in persons:
        linked = person.linked_user
        if linked is not None and linked.is_active:
            if linked.id not in seen_user_ids:
                seen_user_ids.add(linked.id)
                resolved.users.append(linked)
            continue
        email = person.primary_email.strip().lower()
        if not email:
            resolved.skipped.append(person)
        elif email not in seen_emails:
            seen_emails.add(email)
            resolved.guests.append({"email": email, "name": person.display_name})
    return resolved

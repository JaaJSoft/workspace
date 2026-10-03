from django.db import transaction

from ..models import Person
from ..queries import person_with_email, user_persons


def _check_one_scope(owner, group):
    if (owner is None) == (group is None):
        raise ValueError("A person belongs to exactly one owner or one group.")


def create_person(*, owner=None, group=None, **fields):
    _check_one_scope(owner, group)
    return Person.objects.create(owner=owner, group=group, **fields)


def update_person(person, **fields):
    """Write only the columns given: the row may have moved under this instance."""
    for name, value in fields.items():
        setattr(person, name, value)
    person.save(update_fields=[*fields, "updated_at"])
    return person


@transaction.atomic
def move_to_scope(person, *, owner=None, group=None):
    """Re-home a person and drop it from lists that no longer share its scope."""
    _check_one_scope(owner, group)
    person.owner = owner
    person.group = group
    person.save(update_fields=["owner", "group", "updated_at"])
    foreign_lists = person.lists.exclude(owner=owner, group=group)
    for person_list in foreign_lists:
        person_list.members.remove(person)
    return person


def delete_person(person):
    if person.has_avatar:
        from .avatar import delete_avatar

        delete_avatar(person)
    person.delete()


@transaction.atomic
def promote_to_person(user, *, email="", name="", account=None):
    """The user's person for a mail correspondent or a workspace account.

    Returns ``(person, created)``. A person the user can already see that is
    linked to ``account`` or carries the email is reused - linked to
    ``account`` on the way if it has no account yet; otherwise a new one is
    created in the user's own address book.
    """
    persons = user_persons(user)
    if account is not None:
        existing = persons.filter(linked_user=account).first()
        if existing is not None:
            return existing, False
        email = email or account.email
        name = name or account.get_full_name() or account.username
    existing = person_with_email(persons, email)
    if existing is not None:
        if account is not None and existing.linked_user_id is None:
            update_person(existing, linked_user=account)
        return existing, False
    email = email.strip()
    fields = {
        "display_name": (name.strip() or email)[:255],
        "emails": [{"value": email, "type": "other"}] if email else [],
    }
    if account is not None:
        fields.update(
            linked_user=account,
            given_name=account.first_name,
            family_name=account.last_name,
        )
    return create_person(owner=user, **fields), True

from django.db import transaction

from ..models import Person


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

from ..models import PersonList, require_one_scope


class ScopeMismatch(ValueError):
    """A member and its list must belong to the same owner or group."""


def create_list(*, owner=None, group=None, name):
    require_one_scope(owner, group)
    return PersonList.objects.create(owner=owner, group=group, name=name)


def rename_list(person_list, name):
    person_list.name = name
    person_list.save(update_fields=["name"])
    return person_list


def add_members(person_list, persons):
    persons = list(persons)
    for person in persons:
        if (person.owner_id, person.group_id) != (
            person_list.owner_id,
            person_list.group_id,
        ):
            raise ScopeMismatch(
                f"{person.display_name} is not in the same address book as the list."
            )
    person_list.members.add(*persons)


def remove_members(person_list, persons):
    person_list.members.remove(*persons)

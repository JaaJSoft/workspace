from dataclasses import dataclass

from django.db.models import Q

from workspace.common.text import fold_text

from .models import Person, PersonList


def _scope_q(user):
    # Neither branch crosses a join on the person table: ``group__in`` compiles
    # to ``group_id IN (subquery)`` on an indexed column, so the plain OR keeps
    # both indexes usable and the result stays a filterable queryset.
    return Q(owner=user) | Q(group__in=user.groups.all())


def user_persons(user):
    """Persons the user can see and edit: their own and their groups'."""
    return Person.objects.filter(_scope_q(user))


def own_persons(user):
    """The user's personal address book, without their groups' contacts."""
    return Person.objects.filter(owner=user)


def user_person_lists(user):
    return PersonList.objects.filter(_scope_q(user))


def reachable_person(user, uuid):
    return user_persons(user).filter(uuid=uuid).first()


def reachable_list(user, uuid):
    return user_person_lists(user).filter(uuid=uuid).first()


def user_group_ids(user):
    return set(user.groups.values_list("id", flat=True))


def can_edit(user, obj):
    """Owner or group member. Reachability and editability coincide: no roles."""
    if obj.owner_id is not None:
        return obj.owner_id == user.id
    return obj.group_id in user_group_ids(user)


def person_with_email(persons, email):
    """The first person of ``persons`` carrying ``email``, case-insensitively.

    ``search_text`` holds every email folded: a cheap prefilter that also
    matches substrings, so the row's own list decides.
    """
    needle = email.strip().lower()
    if not needle:
        return None
    for person in persons.filter(search_text__contains=fold_text(needle)):
        if any(e.get("value", "").lower() == needle for e in person.emails or []):
            return person
    return None


PERSONAL_BOOK = "personal"
_GROUP_BOOK_PREFIX = "group-"


@dataclass(frozen=True)
class AddressBookScope:
    """One address book as a client sees it: the user's own, or a group's."""

    slug: str
    name: str
    owner: object = None
    group: object = None

    @property
    def scope(self):
        return {"owner": self.owner, "group": self.group}

    def persons(self):
        return Person.objects.filter(**self.scope)

    def lists(self):
        return PersonList.objects.filter(**self.scope)


def _group_book(group):
    return AddressBookScope(
        slug=f"{_GROUP_BOOK_PREFIX}{group.pk}", name=group.name, group=group
    )


def user_address_books(user):
    """The address books the user can sync: their own, then each of their groups'."""
    books = [AddressBookScope(slug=PERSONAL_BOOK, name="My contacts", owner=user)]
    books += [_group_book(group) for group in user.groups.order_by("name")]
    return books


def reachable_address_book(user, slug):
    """The address book *slug* names for the user, or None."""
    if slug == PERSONAL_BOOK:
        return AddressBookScope(slug=PERSONAL_BOOK, name="My contacts", owner=user)
    group_id = slug.removeprefix(_GROUP_BOOK_PREFIX)
    if group_id == slug or not group_id.isdigit():
        return None
    group = user.groups.filter(pk=int(group_id)).first()
    return _group_book(group) if group is not None else None

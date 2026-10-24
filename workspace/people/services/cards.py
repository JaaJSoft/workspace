"""Address book cards in the database: find, list, render and store them.

A card is a person or a list (a ``KIND:group`` card). ``vcard`` turns one into
text and back; this module maps that onto rows of one address book.

Unlike a file import, a card written by a device is never matched by email:
the device holds it as a resource of its own, and folding it into another
contact would make that resource vanish from under it.
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q

from workspace.common.uuids import parse_uuid_or_none

from ..models import SOURCE_CARDDAV, PersonList
from .avatar import avatar_jpeg, delete_avatar, save_avatar_from_card
from .lists import create_list
from .persons import create_person, delete_person, update_person
from .vcard import (
    list_to_vcard,
    list_uid,
    own_uuid,
    person_to_vcard,
    person_uid,
    uid_key,
)

VCARD_SUFFIX = ".vcf"
_URN_PREFIX = "urn:uuid:"


class InvalidCard(ValueError):
    """A card the address book refuses; *condition* names the CardDAV precondition."""

    def __init__(self, message, condition="valid-address-data"):
        super().__init__(message)
        self.condition = condition


@dataclass
class Rendered:
    """A card rendered once, so a query filtering on it does not render it again."""

    card: object
    component: object

    def text(self):
        return self.component.serialize()


def _named(queryset, name):
    found = queryset.filter(dav_name=name).first()
    if found is None and name.endswith(VCARD_SUFFIX):
        uuid = parse_uuid_or_none(name.removesuffix(VCARD_SUFFIX))
        if uuid is not None:
            found = queryset.filter(uuid=uuid, dav_name="").first()
    return found


def find_card(book, name):
    """The person or list served as *name* in *book*, or None."""
    return _named(book.persons(), name) or _named(
        book.lists().prefetch_related("members"), name
    )


def book_cards(book):
    """Every card of *book*: its persons, then its lists."""
    return [
        *book.persons().order_by("uuid"),
        *book.lists().prefetch_related("members").order_by("uuid"),
    ]


def card_uid(card):
    return list_uid(card) if isinstance(card, PersonList) else person_uid(card)


def render_card(card, version):
    if isinstance(card, PersonList):
        component = list_to_vcard(card, card.members.all(), version=version)
        for uid in card.pending_member_uids:
            prop = "X-ADDRESSBOOKSERVER-MEMBER" if version == "3.0" else "member"
            component.add(prop).value = uid
    else:
        photo = avatar_jpeg(card) if card.has_avatar else None
        component = person_to_vcard(
            card, photo=photo, photo_type="jpeg", version=version
        )
    return Rendered(card, component)


def _uid_q(uid):
    """Rows whose UID is *uid*, spelled with or without ``urn:uuid:``, any case."""
    key = uid_key(uid)
    q = Q(import_uid__iexact=key) | Q(import_uid__iexact=f"{_URN_PREFIX}{key}")
    own = own_uuid(uid)
    if own is not None:
        q |= Q(uuid=own, import_uid="")
    return q


def _uid_taken(book, uid, exclude=None):
    for queryset in (book.persons(), book.lists()):
        clash = queryset.filter(_uid_q(uid))
        if exclude is not None:
            clash = clash.exclude(pk=exclude.pk)
        if clash.exists():
            return True
    return False


@transaction.atomic
def store_card(book, name, parsed, existing=None):
    """Write *parsed* as the card *name* of *book*; return the row.

    *existing* is the card currently served under *name*, None for a create.
    """
    is_list = parsed.kind == "group"
    if existing is not None and isinstance(existing, PersonList) != is_list:
        raise InvalidCard("A contact cannot become a list, nor a list a contact.")
    if parsed.uid:
        if existing is not None and uid_key(parsed.uid) != uid_key(card_uid(existing)):
            raise InvalidCard("A card's UID cannot change.", "no-uid-conflict")
        if _uid_taken(book, parsed.uid, exclude=existing):
            raise InvalidCard("Another card already has this UID.", "no-uid-conflict")
    if is_list:
        return _store_list(book, name, parsed, existing)
    return _store_person(book, name, parsed, existing)


def _store_person(book, name, parsed, existing):
    if existing is None:
        person = create_person(
            **book.scope,
            source=SOURCE_CARDDAV,
            dav_name=name,
            import_uid=parsed.uid,
            **parsed.fields,
        )
        _resolve_pending_members(book, person)
    else:
        person = update_person(existing, **parsed.fields)
    _store_photo(person, parsed.photo)
    return person


def _store_photo(person, photo):
    """A card is the whole contact: no photo in it means the photo was removed."""
    if photo is None:
        if person.has_avatar:
            delete_avatar(person)
        return
    if person.has_avatar and photo == avatar_jpeg(person):
        # The image this server sent, sent back: re-encoding it would only
        # lose quality and move the ETag.
        return
    save_avatar_from_card(person, photo)


def _free_list_name(book, name, exclude=None):
    """*name*, or the first ``name (n)`` no other list of the book uses.

    List names are unique per book in the app; a device knows nothing of it.
    """
    taken = book.lists()
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    taken = set(taken.values_list("name", flat=True))
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{name} ({n})"[:255]
        n += 1
    return candidate


def _store_list(book, name, parsed, existing):
    wanted = parsed.fields["display_name"]
    members, pending = [], []
    for uid in parsed.member_uids:
        member = book.persons().filter(_uid_q(uid)).first() if uid else None
        if member is None:
            if uid:
                pending.append(uid)
        elif member not in members:
            members.append(member)

    if existing is None:
        person_list = create_list(**book.scope, name=_free_list_name(book, wanted))
        person_list.import_uid = parsed.uid
        person_list.dav_name = name
        person_list.pending_member_uids = pending
        person_list.save(
            update_fields=["import_uid", "dav_name", "pending_member_uids"]
        )
    else:
        person_list = existing
        changed = []
        if person_list.name != wanted:
            person_list.name = _free_list_name(book, wanted, exclude=person_list)
            changed.append("name")
        if person_list.pending_member_uids != pending:
            person_list.pending_member_uids = pending
            changed.append("pending_member_uids")
        if changed:
            person_list.save(update_fields=changed)
    person_list.members.set(members)
    return person_list


def _resolve_pending_members(book, person):
    """Add a new person to the lists that named it before it existed."""
    uid = person_uid(person)
    key = uid_key(uid)
    for person_list in book.lists().exclude(pending_member_uids=[]):
        still_pending = [
            u for u in person_list.pending_member_uids if uid_key(u) != key
        ]
        if len(still_pending) == len(person_list.pending_member_uids):
            continue
        person_list.pending_member_uids = still_pending
        person_list.save(update_fields=["pending_member_uids"])
        person_list.members.add(person)


def delete_card(card):
    if isinstance(card, PersonList):
        card.delete()
    else:
        delete_person(card)

"""Bring a vCard file into one address book without duplicating what is there.

A person card is matched on its UID first (the ``import_uid`` a previous
import stored, or our own ``urn:uuid:`` when the file is one of our exports),
then on any of its emails. A match is updated from the card; anything else
is created with ``source="import"``. Group cards become lists, their members
resolved by UID against the cards of the same file and the address book.
"""

from dataclasses import dataclass, field
from io import BytesIO

from django.db import transaction
from PIL import Image

from workspace.common.uuids import parse_uuid_or_none

from ..models import SOURCE_IMPORT, Person, PersonList
from .avatar import save_avatar
from .lists import add_members, create_list
from .persons import create_person, update_person
from .vcard import parse_vcards

_UUID_URN_PREFIX = "urn:uuid:"


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    lists: int = 0
    # The rows the person cards became, in card order; not part of the wire report.
    persons: list = field(default_factory=list)

    def as_dict(self):
        return {"created": self.created, "updated": self.updated, "lists": self.lists}


def _uid_key(uid):
    return uid.strip().lower().removeprefix(_UUID_URN_PREFIX)


def _own_uuid(uid):
    """The person uuid a ``urn:uuid:`` UID names, or ``None``."""
    return parse_uuid_or_none(_uid_key(uid)) if uid else None


def _find_by_uid(scope, uid):
    person = Person.objects.filter(**scope, import_uid=uid).first()
    if person is not None:
        return person
    own = _own_uuid(uid)
    if own is not None:
        return Person.objects.filter(**scope, uuid=own).first()
    return None


def _find_by_email(scope, emails):
    for entry in emails:
        value = entry["value"].strip().lower()
        if not value:
            continue
        # search_text carries every email lowercased: a cheap prefilter, and
        # the row's own list decides.
        for person in Person.objects.filter(**scope, search_text__contains=value):
            if any(e.get("value", "").lower() == value for e in person.emails or []):
                return person
    return None


def _match(scope, card):
    if card.uid:
        person = _find_by_uid(scope, card.uid)
        if person is not None:
            return person
    return _find_by_email(scope, card.fields["emails"])


def _apply_photo(person, photo):
    try:
        with Image.open(BytesIO(photo)) as image:
            width, height = image.size
    except OSError, ValueError, Image.DecompressionBombError:
        return
    side = min(width, height)
    try:
        save_avatar(
            person, BytesIO(photo), (width - side) / 2, (height - side) / 2, side, side
        )
    except OSError, ValueError, Image.DecompressionBombError:
        return


def _store_person(scope, card, report):
    person = _match(scope, card)
    fields = dict(card.fields)
    if person is None:
        person = create_person(
            **scope,
            source=SOURCE_IMPORT,
            import_uid=card.uid,
            **fields,
        )
        report.created += 1
    else:
        # A person found by email adopts the card's UID so the next import
        # finds it straight away; our own urn is not an import uid.
        if card.uid and not person.import_uid and _own_uuid(card.uid) != person.uuid:
            fields["import_uid"] = card.uid
        update_person(person, **fields)
        report.updated += 1
    if card.photo is not None:
        _apply_photo(person, card.photo)
    return person


def _store_list(scope, card, members, report):
    name = card.fields["display_name"]
    person_list = PersonList.objects.filter(**scope, name=name).first()
    if person_list is None:
        person_list = create_list(**scope, name=name)
    add_members(person_list, members)
    report.lists += 1
    return person_list


def import_vcards(text, *, owner=None, group=None):
    """Import every card of ``text`` into the address book. Raises VCardError."""
    if (owner is None) == (group is None):
        raise ValueError("An import goes to exactly one owner or one group.")
    scope = {"owner": owner, "group": group}
    cards = parse_vcards(text)
    report = ImportReport()
    with transaction.atomic():
        by_uid = {}
        for card in cards:
            if card.kind != "group":
                person = _store_person(scope, card, report)
                report.persons.append(person)
                by_uid[str(person.uuid)] = person
                if card.uid:
                    by_uid[_uid_key(card.uid)] = person
        for card in cards:
            if card.kind != "group":
                continue
            members = []
            for uid in card.member_uids:
                member = by_uid.get(_uid_key(uid)) or _find_by_uid(scope, uid)
                if member is not None and member not in members:
                    members.append(member)
            _store_list(scope, card, members, report)
    return report

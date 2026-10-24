"""A ``.vcf`` for a person, a list or a whole address book."""

from .avatar import avatar_bytes
from .vcard import list_to_vcard, person_to_vcard, serialize_cards


def export_vcards(persons, lists=()):
    """The persons as 4.0 cards, then every list as a ``KIND:group`` card.

    A list's members are exported as part of the list card only: the caller
    passes the persons it wants as cards of their own.
    """
    cards = [person_to_vcard(p, photo=avatar_bytes(p)) for p in persons]
    for person_list in lists:
        cards.append(list_to_vcard(person_list, person_list.members.all()))
    return serialize_cards(cards)

"""What the people found by their body leave for the user to settle: who
their clothes suggest they are, one review card per suggested person, and
the answers to those cards, which can be undone for a few minutes.
"""

import secrets
from collections import defaultdict
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import transaction

from workspace.people.models import Person

from ..models import Appearance, Face
from ..queries import named_face_clusters, user_appearances
from .face_people import person_cards

# People found by their body shown per suggested person at once, the nearest
# first; the rest come once these are answered.
OUTFITS_PER_PERSON = 60

CONFIRM = "confirm"
REJECT = "reject"
HIDE = "hide"
ASSIGN = "assign"
ACTIONS = (CONFIRM, REJECT, HIDE, ASSIGN)

# Why a person was left as they were.
NO_SUGGESTION = "unavailable"
ALREADY_IN_PHOTO = "already_in_photo"
# The contact has no face in the library, so no page for their photos.
NO_FACE = "no_face"

UNDO_TTL = 10 * 60
_UNDO_KEY = "photos:appearances:undo:{}:{}"
_SAVED_FIELDS = (
    "person_id",
    "suggested_person_id",
    "suggestion_distance",
    "rejected_person_id",
    "hidden",
)


class UndoExpired(Exception):
    """The token names nothing: too old, already used, or someone else's."""


@dataclass(frozen=True)
class PersonOutfits:
    # The PersonCard of the suggested person.
    card: object
    # Nearest first, at most OUTFITS_PER_PERSON.
    appearance_ids: tuple
    total: int


def outfit_suggestions(user):
    """The people found by their body that wait for an answer, by the person
    their clothes suggest, largest first."""
    by_person = defaultdict(list)
    for pk, person_id in (
        user_appearances(user)
        .filter(person__isnull=True, hidden=False, suggested_person__isnull=False)
        .order_by("suggestion_distance", "pk")
        .values_list("pk", "suggested_person_id")
    ):
        by_person[person_id].append(pk)
    if not by_person:
        return []
    clusters = (
        named_face_clusters(user)
        .filter(person__in=list(by_person), photo_count__gt=0)
        .select_related("person")
    )
    return [
        PersonOutfits(
            card=card,
            appearance_ids=tuple(by_person[card.person.pk][:OUTFITS_PER_PERSON]),
            total=len(by_person[card.person.pk]),
        )
        for card in person_cards(clusters)
        if not card.hidden and card.person.pk in by_person
    ]


@dataclass
class BatchResult:
    done: list = field(default_factory=list)
    # (appearance uuid, reason) pairs.
    skipped: list = field(default_factory=list)
    undo: str | None = None


def apply(user, action, appearances, person=None):
    """Answer for each of *appearances* (the user's, already checked).

    confirm: they are *person*, the person their clothes suggest; reject:
    they are not; hide: nobody to name; assign: they are *person*, another
    contact the user can see. A person whose suggestion is no longer
    *person* is skipped: the suggestions move in the background, and the
    answer is about the person the user was shown. So is a person the photo
    already shows (a person is in a photo once), and everyone assigned to a
    contact with no face in the library: their photos are listed through
    their faces. A person confirmed as someone the photo already shows
    cannot be them, so they are set apart from that person as by a reject:
    otherwise the next pass suggests the same name and the review asks again.
    """
    result = BatchResult()
    if not appearances:
        return result
    if action == ASSIGN and not (
        named_face_clusters(user).filter(person=person, photo_count__gt=0).exists()
    ):
        result.skipped = [(row.pk, NO_FACE) for row in appearances]
        return result
    result.undo = _save_undo(user, appearances)
    changed = False
    with transaction.atomic():
        for row in appearances:
            reason = _answer(row, action, person)
            if reason is None:
                result.done.append(row.pk)
            else:
                result.skipped.append((row.pk, reason))
            set_apart = action == CONFIRM and reason == ALREADY_IN_PHOTO
            changed = changed or reason is None or set_apart
    if not changed:
        cache.delete(_UNDO_KEY.format(user.pk, result.undo))
        result.undo = None
    return result


def _answer(row, action, person):
    """Write *action*'s answer on *row*; the reason it could not, or None."""
    if action in (CONFIRM, REJECT) and row.suggested_person_id != person.pk:
        return NO_SUGGESTION
    person_id = person.pk if action in (CONFIRM, ASSIGN) else None
    if person_id is not None and person_id in _persons_in_photo(row):
        if action != CONFIRM:
            return ALREADY_IN_PHOTO
        action = REJECT
        reason = ALREADY_IN_PHOTO
    else:
        reason = None
    if action == REJECT:
        row.rejected_person_id = person.pk
    elif action == HIDE:
        row.hidden = True
    else:
        row.person_id = person_id
        row.hidden = False
    row.suggested_person_id = None
    row.suggestion_distance = None
    row.save(update_fields=_fields(_SAVED_FIELDS))
    return reason


def _fields(names):
    return [name.removesuffix("_id") for name in names]


def _persons_in_photo(row):
    """The persons the photo of *row* already shows: its named faces, and the
    other people the user said who they are."""
    faces = Face.objects.filter(
        file_id=row.file_id, cluster__person__isnull=False
    ).values_list("cluster__person_id", flat=True)
    others = (
        Appearance.objects.filter(file_id=row.file_id, person__isnull=False)
        .exclude(pk=row.pk)
        .values_list("person_id", flat=True)
    )
    return set(faces) | set(others)


def _save_undo(user, appearances):
    state = [
        {
            "uuid": str(row.pk),
            **{name: _plain(getattr(row, name)) for name in _SAVED_FIELDS},
        }
        for row in appearances
    ]
    token = secrets.token_urlsafe(16)
    cache.set(_UNDO_KEY.format(user.pk, token), state, UNDO_TTL)
    return token


def _plain(value):
    return (
        str(value)
        if value is not None and not isinstance(value, (bool, float))
        else value
    )


def undo(user, token):
    """Put the people of the batch *token* names back as they were; return
    how many went back."""
    key = _UNDO_KEY.format(user.pk, token)
    state = cache.get(key)
    if state is None:
        raise UndoExpired
    cache.delete(key)
    saved = {row["uuid"]: row for row in state}
    # A contact deleted since cannot be pointed at again.
    mentioned = {
        value
        for row in state
        for name in ("person_id", "suggested_person_id", "rejected_person_id")
        if (value := row[name]) is not None
    }
    existing = {
        str(pk)
        for pk in Person.objects.filter(pk__in=mentioned).values_list("pk", flat=True)
    }
    restored = 0
    with transaction.atomic():
        for row in Appearance.objects.filter(owner=user, pk__in=list(saved)):
            for name in _SAVED_FIELDS:
                value = saved[str(row.pk)][name]
                if name.endswith("person_id") and value not in existing:
                    value = None
                setattr(row, name, value)
            row.save(update_fields=_fields(_SAVED_FIELDS))
            restored += 1
    return restored

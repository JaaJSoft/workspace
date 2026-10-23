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

from ..models import Appearance
from ..queries import named_face_clusters, user_appearances
from .appearance_suggestions import known_by_faces
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


@dataclass(frozen=True)
class SilhouettePage:
    appearance_ids: tuple
    total: int


# People found by their body shown on a person's page at once.
PERSON_SILHOUETTES_PER_PAGE = 300


def person_silhouettes(user, person):
    """The people found by their body the user said are *person*, the most
    recently found first."""
    rows = user_appearances(user).filter(person=person, hidden=False)
    ids = rows.order_by("-created_at", "pk").values_list("pk", flat=True)
    return SilhouettePage(
        appearance_ids=tuple(ids[:PERSON_SILHOUETTES_PER_PAGE]), total=rows.count()
    )


@dataclass
class BatchResult:
    done: list = field(default_factory=list)
    # (appearance uuid, reason) pairs.
    skipped: list = field(default_factory=list)
    undo: str | None = None


def apply(user, action, appearances, person=None):
    """Answer for each of *appearances* (the user's, already checked).

    confirm: they are *person*, the person their clothes suggest; reject:
    they are not *person* - the one suggested, the one the user said they
    were, or the one the review asked about without a suggestion (the
    person of their folder, silhouettes.py); hide: nobody to name; assign:
    they are *person*, another contact the user can see. A person suggested
    someone else than *person*, or said to be someone else, is skipped: the
    suggestions move in the background, and the answer is about the person
    the user was shown. So is a person confirmed or assigned as someone
    another person of the photo already is (a person is in a photo once) -
    whose face is on another head, or whom the user named already - and
    everyone assigned to a contact with no face in the library: their photos
    are listed through their faces. A face on nobody's head may be theirs:
    someone crouching or bent over wears it lower. A person confirmed or
    assigned as someone the photo already shows is still set apart from the
    person suggested, or from that person when nobody was, as by a reject,
    and hidden: the user recognized them, and the review would otherwise ask
    the same question again.
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
    worn = _worn_faces(user, appearances)
    changed = False
    with transaction.atomic():
        for row in appearances:
            reason = _answer(row, action, person, worn)
            if reason is None:
                result.done.append(row.pk)
            else:
                result.skipped.append((row.pk, reason))
            changed = changed or reason in (None, ALREADY_IN_PHOTO)
    if not changed:
        cache.delete(_UNDO_KEY.format(user.pk, result.undo))
        result.undo = None
    return result


def _answer(row, action, person, worn):
    """Write *action*'s answer on *row*; the reason it could not, or None.
    *worn* is _worn_faces() of the batch."""
    if action == CONFIRM and row.suggested_person_id != person.pk:
        return NO_SUGGESTION
    if action == REJECT and not _may_reject(row, person):
        return NO_SUGGESTION
    person_id = person.pk if action in (CONFIRM, ASSIGN) else None
    not_them = person.pk if action == REJECT else None
    reason = None
    if person_id is not None and person_id in _persons_in_photo(row, worn):
        # Whoever the user named, they said the suggestion is not them.
        action = REJECT
        not_them = row.suggested_person_id or person_id
        reason = ALREADY_IN_PHOTO
    if action == REJECT:
        # One name is kept: the last the user said they are not.
        row.rejected_person_id = not_them
        if row.person_id == not_them:
            row.person_id = None
        if reason == ALREADY_IN_PHOTO:
            # Someone the photo already shows - a second box of them, or
            # their face put on another head: nobody new to ask about, and
            # no longer whoever the user said they were, as by a hide.
            row.hidden = True
            row.person_id = None
    elif action == HIDE:
        # Nobody to name: kept as someone's, they would still teach the
        # suggestions that person's clothes.
        row.hidden = True
        row.person_id = None
    else:
        row.person_id = person_id
        row.hidden = False
    row.suggested_person_id = None
    row.suggestion_distance = None
    row.save(update_fields=_fields(_SAVED_FIELDS))
    return reason


def _may_reject(row, person):
    """Whether *row* is still about *person*: suggested as them, said to be
    them, or neither and asked about as them."""
    if person.pk in (row.suggested_person_id, row.person_id):
        return True
    return row.suggested_person_id is None and row.person_id is None


def _fields(names):
    return [name.removesuffix("_id") for name in names]


@dataclass(frozen=True)
class _Body:
    pk: object
    file_id: object
    box: tuple
    person_id: object


def _worn_faces(user, rows):
    """{file id: {appearance pk: the person whose face is on them}} for
    everyone in the photos of *rows*, as the suggestions read it
    (appearance_suggestions.known_by_faces)."""
    files = {row.file_id for row in rows}
    bodies = [
        _Body(pk=pk, file_id=file_id, box=(x, y, w, h), person_id=person_id)
        for pk, file_id, x, y, w, h, person_id in Appearance.objects.filter(
            owner=user, file_id__in=files
        ).values_list(
            "pk", "file_id", "box_x", "box_y", "box_width", "box_height", "person_id"
        )
    ]
    named, _ = known_by_faces(user, bodies, files=list(files))
    file_of = {body.pk: body.file_id for body in bodies}
    worn = defaultdict(dict)
    for pk, who in named.items():
        if not isinstance(who, tuple):
            worn[file_of[pk]][pk] = who
    return worn


def _persons_in_photo(row, worn):
    """The persons another person of the photo of *row* already is: their
    face is on that person's head, or the user said who that person is."""
    on_others = {who for pk, who in worn.get(row.file_id, {}).items() if pk != row.pk}
    said = (
        Appearance.objects.filter(file_id=row.file_id, person__isnull=False)
        .exclude(pk=row.pk)
        .values_list("person_id", flat=True)
    )
    return on_others | set(said)


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

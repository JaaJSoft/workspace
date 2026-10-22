"""The people found by their body that nothing names: no face of theirs is
grouped or good enough to ask about, and their clothes suggest nobody. The
review asks the user who they are, one folder and one outfit at a time.

A folder is often one event, and often one person's. A group of people each
alone in their photo is asked about as the person the folder shows in most
of its photos - but only when they are dressed like that person in another
photo of the folder: the folder alone is wrong one time in two (the
appearance bench's "most photographed instead"). Nothing is stored until
the user answers (appearance_review.apply).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from django.db.models import Q

from workspace.common.vectors.encoding import from_bytes
from workspace.files.models import File

from ..models import Face
from ..queries import library_files, named_face_clusters, user_appearances
from .appearance_suggestions import known_by_faces
from .embedding.registry import get_embedding_backend
from .face_people import person_cards
from .face_preferences import faces_enabled
from .face_review import likeness_groups

# People found by their body shown per group at once; the rest come once
# these are answered.
SILHOUETTES_PER_GROUP = 60
# The most confident people read per folder: a dump of a phone's camera
# would otherwise compare thousands of torsos with each other.
SILHOUETTES_PER_FOLDER = 500
# The folder's person shows in at least this many of its photos, and in more
# than half of those showing someone named.
MIN_FOLDER_PHOTOS = 3


@dataclass(frozen=True)
class SilhouetteGroup:
    folder_id: object
    # None for the top of the user's files.
    folder_name: str | None
    # Whether each of them is the only person of their photo.
    alone: bool
    # The PersonCard of who they may be, or None.
    guess: object
    appearance_ids: tuple
    total: int


@dataclass(eq=False)
class _Row:
    pk: object
    file_id: object
    folder_id: object
    box: tuple
    # Who the user said it is, None once hidden.
    person_id: object
    rejected_id: object
    # Nobody answered for them, and their clothes suggest nobody.
    waiting: bool
    embedding: bytes


def silhouette_groups(user):
    """The people found by their body nobody named, by folder and outfit,
    largest groups first. Somebody dressed like nobody else of their folder
    is asked about only when alone in their photo: the subject of it, not a
    passer-by."""
    backend = get_embedding_backend()
    threshold = backend.appearance_max_distance
    if threshold is None or not faces_enabled(user):
        return []
    rows = user_appearances(user).filter(
        file__appearance_analysis__model=backend.model_key, embedding__isnull=False
    )
    folders = set(
        rows.filter(person__isnull=True, hidden=False, suggested_person__isnull=True)
        .values_list("file__parent_id", flat=True)
        .distinct()
    )
    if not folders:
        return []
    rows = _in_folders(rows, folders, "file__parent")
    seen = _read(rows)
    named, in_photo = known_by_faces(user, seen, files=rows.values("file_id"))
    people_per_photo = Counter(row.file_id for row in seen)
    by_folder = defaultdict(list)
    # The people the folder's other photos name, by their face or as the
    # user said.
    anchors = defaultdict(list)
    for row in seen:
        if row.waiting and row.pk not in named:
            by_folder[row.folder_id].append(row)
        who = row.person_id if row.person_id is not None else named.get(row.pk)
        if who is not None and not isinstance(who, tuple):
            anchors[row.folder_id, who].append(row)
    majority = _folder_persons(user, set(by_folder))
    dims = backend.dims
    asked = []
    for folder_id, people in by_folder.items():
        people = people[:SILHOUETTES_PER_FOLDER]
        alone = [
            row
            for row in people
            if people_per_photo[row.file_id] == 1 and not in_photo.get(row.file_id)
        ]
        person_id = majority.get(folder_id)
        for group in _outfits(alone, threshold, dims):
            guess = person_id
            if guess is not None and (
                any(row.rejected_id == guess for row in group)
                or not _dressed_like(group, anchors[folder_id, guess], threshold, dims)
            ):
                guess = None
            asked.append((folder_id, True, group, guess))
        lone = set(alone)
        others = [row for row in people if row not in lone]
        asked += [
            (folder_id, False, group, None)
            for group in _outfits(others, threshold, dims)
            # Dressed like nobody else: someone in the background.
            if len(group) > 1
        ]
    cards = _cards(user, {guess for *_, guess in asked if guess is not None})
    names = dict(
        File.objects.filter(pk__in=[f for f in by_folder if f is not None]).values_list(
            "pk", "name"
        )
    )
    groups = [
        SilhouetteGroup(
            folder_id=folder_id,
            folder_name=names.get(folder_id),
            alone=alone,
            guess=cards.get(guess),
            appearance_ids=tuple(row.pk for row in group[:SILHOUETTES_PER_GROUP]),
            total=len(group),
        )
        for folder_id, alone, group, guess in asked
    ]
    groups.sort(key=lambda group: (-group.total, group.guess is None))
    return groups


def _in_folders(rows, folders, field):
    """*rows* whose *field* is one of *folders*; None is the top of the
    files."""
    within = Q(**{f"{field}_id__in": [f for f in folders if f is not None]})
    if None in folders:
        within |= Q(**{f"{field}__isnull": True})
    return rows.filter(within)


def _read(rows):
    """*rows* as _Row, the most confident first."""
    return [
        _Row(
            pk=pk,
            file_id=file_id,
            folder_id=folder_id,
            box=(x, y, w, h),
            person_id=None if hidden else person_id,
            rejected_id=rejected_id,
            waiting=person_id is None and not hidden and suggested_id is None,
            embedding=embedding,
        )
        for (
            pk,
            file_id,
            folder_id,
            x,
            y,
            w,
            h,
            person_id,
            rejected_id,
            hidden,
            suggested_id,
            embedding,
        ) in rows.order_by("-detector_score", "pk").values_list(
            "pk",
            "file_id",
            "file__parent_id",
            "box_x",
            "box_y",
            "box_width",
            "box_height",
            "person_id",
            "rejected_person_id",
            "hidden",
            "suggested_person_id",
            "embedding",
        )
    ]


def _outfits(rows, threshold, dims):
    """*rows* gathered by what they wear."""
    by_pk = {row.pk: row for row in rows}
    return [
        [by_pk[pk] for pk in group]
        for group in likeness_groups(
            [(row.pk, row.embedding) for row in rows], threshold, dims
        )
    ]


def _dressed_like(group, anchors, threshold, dims):
    """Whether someone of *group* is dressed like one of *anchors*."""
    if not anchors:
        return False
    mine = np.stack([from_bytes(row.embedding, dims) for row in group])
    theirs = np.stack([from_bytes(row.embedding, dims) for row in anchors])
    return float((1 - mine @ theirs.T).min()) <= threshold


def _folder_persons(user, folders):
    """{folder id: the person in most of its photos}, for those of *folders*
    where one is in more than half of the photos showing someone named, and
    in MIN_FOLDER_PHOTOS of them at least."""
    if not folders:
        return {}
    files = _in_folders(library_files(user), folders, "parent").values("pk")
    # A set: a person shown by their face and as the user said counts once.
    shown = set(
        Face.objects.filter(
            owner=user,
            file__in=files,
            cluster__in=named_face_clusters(user).filter(hidden=False).values("pk"),
        ).values_list("file__parent_id", "file_id", "cluster__person_id")
    ) | set(
        user_appearances(user)
        .filter(file__in=files, person__isnull=False, hidden=False)
        .values_list("file__parent_id", "file_id", "person_id")
    )
    photos = defaultdict(set)
    counts = defaultdict(Counter)
    for folder_id, file_id, person_id in shown:
        photos[folder_id].add(file_id)
        counts[folder_id][person_id] += 1
    persons = {}
    for folder_id, counter in counts.items():
        ((person_id, count),) = counter.most_common(1)
        if count >= MIN_FOLDER_PHOTOS and 2 * count > len(photos[folder_id]):
            persons[folder_id] = person_id
    return persons


def _cards(user, person_ids):
    """{person id: PersonCard} for those of *person_ids* the user still sees
    in their People, not hidden."""
    if not person_ids:
        return {}
    clusters = (
        named_face_clusters(user)
        .filter(person__in=list(person_ids), photo_count__gt=0)
        .select_related("person")
    )
    return {card.person.pk: card for card in person_cards(clusters) if not card.hidden}

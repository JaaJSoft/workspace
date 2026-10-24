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

import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from django.db.models import Count, Exists, OuterRef, Q

from workspace.common.vectors.encoding import from_bytes
from workspace.files.models import File

from ..models import Appearance, Face
from ..queries import library_files, user_appearances
from .appearance_suggestions import is_double, known_by_faces
from .embedding.registry import get_embedding_backend
from .face_people import person_cards
from .face_preferences import faces_enabled
from .face_review import ReviewReads, likeness_groups

# People found by their body shown per group at once; the rest come once
# these are answered.
SILHOUETTES_PER_GROUP = 60
# The most confident people read per folder: a dump of a phone's camera
# would otherwise compare thousands of torsos with each other.
SILHOUETTES_PER_FOLDER = 500
# The folder's person shows in at least this many of its photos, and in more
# than half of those showing someone named.
MIN_FOLDER_PHOTOS = 3
# Embeddings read per query.
_BATCH = 500


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
    # Read by _load_embeddings(), for the rows compared by their clothes: a
    # torso's is kilobytes, and the library's would be read for a few.
    embedding: bytes | None = None


def silhouette_groups(user, reads=None):
    """The people found by their body nobody named, by folder and outfit,
    largest groups first. Somebody dressed like nobody else of their folder
    is asked about only when alone in their photo: the subject of it, not a
    passer-by."""
    backend = get_embedding_backend()
    threshold = backend.appearance_max_distance
    if threshold is None or not faces_enabled(user):
        return []
    reads = reads or ReviewReads(user)
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
    named, in_photo = known_by_faces(
        user, seen, files=rows.values("file_id"), doubtful=reads.doubtful_face_ids
    )
    people_per_photo = Counter(
        row.file_id for row in seen if not is_double(named.get(row.pk))
    )
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
    majority = _folder_persons(user, set(by_folder), reads.named)
    for folder_id in by_folder:
        by_folder[folder_id] = by_folder[folder_id][:SILHOUETTES_PER_FOLDER]
    dims = backend.dims
    folder_anchors = {
        folder_id: anchors.get((folder_id, person_id), [])
        for folder_id, person_id in majority.items()
    }
    _load_embeddings(
        itertools.chain(
            itertools.chain.from_iterable(by_folder.values()),
            itertools.chain.from_iterable(folder_anchors.values()),
        )
    )
    asked = []
    for folder_id, people in by_folder.items():
        alone = [
            row
            for row in people
            if people_per_photo[row.file_id] == 1 and not in_photo.get(row.file_id)
        ]
        person_id = majority.get(folder_id)
        # Decoded once per folder: every group of it is compared with them.
        theirs = None
        for group in _outfits(alone, threshold, dims):
            guess = person_id
            if guess is not None and any(row.rejected_id == guess for row in group):
                guess = None
            if guess is not None:
                if theirs is None:
                    theirs = _matrix(folder_anchors[folder_id], dims)
                if not _dressed_like(group, theirs, threshold, dims):
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
    cards = _cards({guess for *_, guess in asked if guess is not None}, reads.named)
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
        )
    ]


def _load_embeddings(rows):
    """Read the embedding of each of *rows*, _Row, that has none yet."""
    missing = {row.pk: row for row in rows if row.embedding is None}
    for chunk in itertools.batched(missing, _BATCH, strict=False):
        for pk, blob in Appearance.objects.filter(pk__in=chunk).values_list(
            "pk", "embedding"
        ):
            missing[pk].embedding = blob


def _outfits(rows, threshold, dims):
    """*rows* gathered by what they wear."""
    by_pk = {row.pk: row for row in rows}
    return [
        [by_pk[pk] for pk in group]
        for group in likeness_groups(
            [(row.pk, row.embedding) for row in rows], threshold, dims
        )
    ]


def _matrix(rows, dims):
    """The embeddings of *rows*, one per line; None when none has one - a
    row deleted since it was read (its photo read again) has none."""
    vectors = [from_bytes(row.embedding, dims) for row in rows if row.embedding]
    return np.stack(vectors) if vectors else None


def _dressed_like(group, theirs, threshold, dims):
    """Whether someone of *group* is dressed like one of the people whose
    embeddings are *theirs*, _matrix()'s."""
    mine = _matrix(group, dims)
    if mine is None or theirs is None:
        return False
    return float((1 - mine @ theirs.T).min()) <= threshold


def _folder_persons(user, folders, clusters):
    """{folder id: the person in most of its photos}, for those of *folders*
    where one is in more than half of the photos showing someone named, and
    in MIN_FOLDER_PHOTOS of them at least. *clusters* are
    ReviewReads.named.

    Counted by the database: a library's folders hold tens of thousands of
    faces, and each row read costs more than its share of a count."""
    if not folders:
        return {}
    files = _in_folders(library_files(user), folders, "parent")
    named_clusters = [c.pk for c in clusters if not c.hidden]
    named_faces = Face.objects.filter(owner=user, cluster__in=named_clusters)
    named_bodies = Appearance.objects.filter(
        owner=user, person__isnull=False, hidden=False
    )
    shown = files.filter(
        Q(pk__in=named_faces.values("file")) | Q(pk__in=named_bodies.values("file"))
    )
    photos = dict(_photos_per(shown, "parent_id", photo="pk"))
    faces = named_faces.filter(file__in=files.values("pk"))
    said = named_bodies.filter(file__in=files.values("pk"))
    # A person shown by their face and as the user said counts once. Looked
    # up by photo first: led by its owner and cluster instead, SQLite reads
    # every named face of the library for each person said.
    both = said.filter(
        Exists(
            Face.objects.filter(
                file=OuterRef("file"),
                cluster__in=named_clusters,
                cluster__person=OuterRef("person"),
            )
        )
    )
    counts = defaultdict(Counter)
    for folder_id, person_id, count in _photos_per(
        faces, "file__parent_id", "cluster__person_id", photo="file"
    ):
        counts[folder_id][person_id] += count
    for folder_id, person_id, count in _photos_per(
        said, "file__parent_id", "person_id", photo="file"
    ):
        counts[folder_id][person_id] += count
    for folder_id, person_id, count in _photos_per(
        both, "file__parent_id", "person_id", photo="file"
    ):
        counts[folder_id][person_id] -= count
    persons = {}
    for folder_id, counter in counts.items():
        # Ties go to the same person on every load.
        person_id, count = max(
            counter.items(), key=lambda item: (item[1], -int(item[0]))
        )
        if count >= MIN_FOLDER_PHOTOS and 2 * count > photos.get(folder_id, 0):
            persons[folder_id] = person_id
    return persons


def _photos_per(rows, *group_by, photo):
    """(*group_by values..., how many distinct *photo* they hold) for each
    group of *rows*."""
    return (
        rows.order_by()
        .values(*group_by)
        .annotate(photo_count=Count(photo, distinct=True))
        .values_list(*group_by, "photo_count")
    )


def _cards(person_ids, clusters):
    """{person id: PersonCard} for those of *person_ids* the user still sees
    in their People, not hidden, from *clusters* (ReviewReads.named)."""
    if not person_ids:
        return {}
    theirs = [c for c in clusters if c.person_id in person_ids]
    return {card.person.pk: card for card in person_cards(theirs) if not card.hidden}

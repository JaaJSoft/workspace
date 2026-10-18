"""Suggest who the people found by their body are, from what they wear.

People keep their clothes for a few hours. A person no grouped face
accounts for is suggested as the named person whose torso, in another photo
taken within WINDOW of theirs, is the nearest: when it is near enough (the
backend's ``appearance_max_distance``) and nearer than anybody else's by the
backend's ``appearance_margin``. The anchors are the people whose face is
in a named group, and those the user confirmed.

Everyone whose face can tell who they are is somebody, named or not: an
unnamed group, a face the face review asks about. They are never suggested,
but someone dressed like one of them is not suggested as anybody else
either - in a library where few people are named, the stranger sitting by a
named person would otherwise take their name. The people asked about are
those seen without a face, or with one too poor to recognize anyone by.

Only ever a suggestion: the review asks, and a photo is the person's once
the user says yes (``Appearance.person``).
"""

from __future__ import annotations

import itertools
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from django.contrib.auth import get_user_model
from django.db.models import F, Q

from workspace.common.vectors.encoding import from_bytes

from ..models import Appearance, Face, MediaItem
from ..queries import library_files, user_face_clusters
from .embedding.registry import get_embedding_backend
from .face_grouping import LOW_QUALITY
from .face_preferences import faces_enabled

# How far apart two photos may be taken for clothes to tell who is who.
WINDOW = timedelta(hours=6)
# A face is a person's when its centre is inside the person's box, within
# the top HEAD_ZONE of its height: the head of someone standing or sitting.
HEAD_ZONE = 0.35
# People answered per pass over the window, and embeddings read per query.
_BATCH = 500


@dataclass
class _Seen:
    """One person of the pass, as read from their row."""

    pk: object
    file_id: object
    taken: datetime
    box: tuple
    person_id: object
    rejected_id: object
    hidden: bool
    suggested_id: object


def suggest_owner(owner_id):
    """Suggest who every person of *owner_id*'s library is; return how many
    suggestions changed."""
    user = get_user_model().objects.filter(pk=owner_id).first()
    return 0 if user is None else _suggest(user)


def suggest_around(file_obj):
    """Suggest again who the people near *file_obj* are, in time: its own,
    and those its people may now name."""
    taken = (
        MediaItem.objects.filter(file_id=file_obj.pk)
        .values_list("taken_at", flat=True)
        .first()
    )
    if taken is None:
        return 0
    return _suggest(file_obj.owner, taken - WINDOW, taken + WINDOW)


def _suggest(user, start=None, end=None):
    """Suggest who the people of *user*'s library taken from *start* to *end*
    are (all of them by default); return how many suggestions changed."""
    backend = get_embedding_backend()
    threshold = backend.appearance_max_distance
    if threshold is None or not faces_enabled(user):
        return 0
    seen = _read(user, backend.model_key, start, end)
    if not seen:
        return 0
    named, in_photo = _known_by_faces(user, seen, start, end)
    for item in seen:
        if item.person_id is not None:
            named[item.pk] = item.person_id
            in_photo[item.file_id].add(item.person_id)
    anchors = sorted((item for item in seen if item.pk in named), key=_by_time)
    queries = sorted(
        (
            item
            for item in seen
            if item.pk not in named
            and not item.hidden
            and (start is None or start <= item.taken <= end)
        ),
        key=_by_time,
    )
    times = [item.taken for item in anchors]
    vectors = {}
    # Whoever is no longer asked about loses the suggestion they had: their
    # face got a group, or the user hid them.
    asked = {item.pk for item in queries}
    answers = [
        (item, None)
        for item in seen
        if item.suggested_id is not None
        and item.pk not in asked
        and (start is None or start <= item.taken <= end)
    ]
    for batch in itertools.batched(queries, _BATCH, strict=False):
        window = anchors[
            bisect_left(times, batch[0].taken - WINDOW) : bisect_right(
                times, batch[-1].taken + WINDOW
            )
        ]
        # Only the window is kept: the vectors of a whole library would not fit.
        wanted = {item.pk for item in window} | {item.pk for item in batch}
        vectors = {pk: vector for pk, vector in vectors.items() if pk in wanted}
        vectors.update(_vectors(wanted - vectors.keys(), backend.dims))
        for query in batch:
            suggestion = _nearest(
                query,
                window,
                named,
                in_photo,
                vectors,
                threshold,
                backend.appearance_margin,
            )
            answers.append((query, suggestion))
    return sum(_write(query, suggestion) for query, suggestion in _once(answers))


def _once(answers):
    """*answers* with each person suggested once per photo, for the nearest
    of the people there: a person is in a photo once."""
    nearest = {}
    for query, suggestion in answers:
        if suggestion is not None:
            key = (query.file_id, suggestion[0])
            if key not in nearest or suggestion[1] < nearest[key][1]:
                nearest[key] = (query.pk, suggestion[1])
    return [
        (
            query,
            suggestion
            if suggestion is not None
            and nearest[query.file_id, suggestion[0]][0] == query.pk
            else None,
        )
        for query, suggestion in answers
    ]


def _by_time(item):
    return item.taken


def _read(user, model_key, start, end):
    rows = Appearance.objects.filter(
        owner=user,
        file__in=library_files(user).values("pk"),
        file__appearance_analysis__model=model_key,
        embedding__isnull=False,
        file__media_item__taken_at__isnull=False,
    )
    if start is not None:
        rows = rows.filter(
            file__media_item__taken_at__range=(start - WINDOW, end + WINDOW)
        )
    return [
        _Seen(
            pk=pk,
            file_id=file_id,
            taken=taken,
            box=(x, y, w, h),
            person_id=person_id,
            rejected_id=rejected_id,
            hidden=hidden,
            suggested_id=suggested_id,
        )
        for pk, file_id, taken, x, y, w, h, person_id, rejected_id, hidden, suggested_id in (
            rows.values_list(
                "pk",
                "file_id",
                "file__media_item__taken_at",
                "box_x",
                "box_y",
                "box_width",
                "box_height",
                "person_id",
                "rejected_person_id",
                "hidden",
                "suggested_person_id",
            )
        )
    ]


def _known_by_faces(user, seen, start, end):
    """({appearance pk: who} for the people whose face was seen,
    {file id: the persons its named faces show}).

    Who is the person a visible group is named after; ("cluster", its uuid)
    for an unnamed or hidden group, and ("face", its uuid) for a face in no
    group that is good enough for the face review to ask about: somebody,
    but nobody to suggest. A blurred face in no group says nothing.
    """
    who = {
        pk: person_id if person_visible and not hidden else ("cluster", pk)
        for pk, person_id, person_visible, hidden in user_face_clusters(
            user
        ).values_list("pk", "person_id", "person_visible", "hidden")
    }
    # A duplicate stands for someone another face of the photo shows.
    faces = (
        Face.objects.filter(owner=user)
        .filter(
            Q(cluster__isnull=False)
            | ~Q(assignment=Face.Assignment.AUTO)
            | Q(quality__gte=LOW_QUALITY)
        )
        .exclude(assignment=Face.Assignment.DUPLICATE)
        # Grouped faces first: they claim the body under them before an
        # ungrouped face nearby does.
        .order_by(F("cluster").asc(nulls_last=True), "pk")
    )
    if start is not None:
        faces = faces.filter(
            file__media_item__taken_at__range=(start - WINDOW, end + WINDOW)
        )
    by_file = defaultdict(list)
    for item in seen:
        by_file[item.file_id].append(item)
    named, in_photo = {}, defaultdict(set)
    for pk, file_id, x, y, w, h, cluster_id in faces.values_list(
        "pk", "file_id", "box_x", "box_y", "box_width", "box_height", "cluster_id"
    ):
        key = who.get(cluster_id, ("face", pk))
        if not isinstance(key, tuple):
            in_photo[file_id].add(key)
        owner = _wearer((x + w / 2, y + h / 2), by_file.get(file_id, ()), named)
        if owner is not None:
            named[owner.pk] = key
    return named, in_photo


def _wearer(centre, people, taken):
    """The person of *people* whose head is at *centre*, among those not in
    *taken* yet; None when nobody's is."""
    fx, fy = centre
    best, best_gap = None, None
    for item in people:
        if item.pk in taken:
            continue
        x, y, w, h = item.box
        if not (x <= fx <= x + w and y - 0.05 * h <= fy <= y + HEAD_ZONE * h):
            continue
        gap = abs(fx - (x + w / 2)) / w + abs(fy - y) / h
        if best is None or gap < best_gap:
            best, best_gap = item, gap
    return best


def _vectors(pks, dims):
    vectors = {}
    for chunk in itertools.batched(pks, _BATCH, strict=False):
        for pk, blob in Appearance.objects.filter(pk__in=chunk).values_list(
            "pk", "embedding"
        ):
            vector = from_bytes(blob, dims) if blob else None
            if vector is not None:
                vectors[pk] = vector
    return vectors


def _nearest(query, window, named, in_photo, vectors, threshold, margin):
    """(person id, distance) the clothes of *query* suggest, or None."""
    vector = vectors.get(query.pk)
    if vector is None:
        return None
    excluded = in_photo.get(query.file_id, set()) | {query.rejected_id}
    candidates = [
        item
        for item in window
        if item.file_id != query.file_id
        and abs(item.taken - query.taken) <= WINDOW
        and named[item.pk] not in excluded
        and item.pk in vectors
    ]
    if not candidates:
        return None
    distances = 1 - np.stack([vectors[item.pk] for item in candidates]) @ vector
    order = np.argsort(distances, kind="stable")
    best = named[candidates[order[0]].pk]
    if isinstance(best, tuple):
        # Dressed like someone whose name the user has not given.
        return None
    nearest = float(distances[order[0]])
    rival = next(
        (float(distances[i]) for i in order if named[candidates[i].pk] != best),
        None,
    )
    if nearest > threshold or (rival is not None and rival - nearest < margin):
        return None
    return best, nearest


def _write(query, suggestion):
    """Store *suggestion* for *query* if it changed; 1 when it did."""
    person_id, distance = suggestion if suggestion is not None else (None, None)
    if person_id == query.suggested_id:
        return 0
    # Conditional on what the pass read: a person the user answered for in
    # the meantime (said who it is, or is not, or hid) keeps the answer.
    return Appearance.objects.filter(
        pk=query.pk,
        person__isnull=True,
        hidden=False,
        suggested_person_id=query.suggested_id,
        rejected_person_id=query.rejected_id,
    ).update(
        suggested_person_id=person_id,
        suggestion_distance=None if distance is None else round(distance, 4),
    )

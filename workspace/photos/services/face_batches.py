"""Corrections applied to a selection of faces at once, and their undo.

A batch never fails as a whole: each face that cannot take the correction
(another face of its photo is already that person, the action does not apply
to it) is skipped with a reason, and the rest go through.

Before touching anything a batch saves how its faces and their clusters were,
under a token kept a few minutes in the cache. Undoing puts them back, a
cluster the batch emptied (and so deleted) included.
"""

import secrets
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import IntegrityError, transaction

from workspace.people.models import Person
from workspace.people.services.persons import create_person

from ..actions import FaceActionRegistry
from ..models import Face, FaceCluster
from . import face_corrections, face_people
from .face_corrections import CorrectionError, PhotoAlreadyInCluster
from .face_grouping import photo_clusters, refresh_clusters

UNDO_TTL = 10 * 60
_UNDO_KEY = "photos:faces:undo:{}:{}"

CONFIRM = "confirm"
REJECT = "reject"
HIDE = "hide"
UNHIDE = "unhide"
ASSIGN = "assign"

# Why a face was left as it was.
UNAVAILABLE = "unavailable"
ALREADY_IN_PHOTO = "already_in_photo"


class UndoExpired(Exception):
    """The token names nothing: too old, already used, or someone else's."""


@dataclass
class BatchResult:
    done: list = field(default_factory=list)
    # (face uuid, reason) pairs.
    skipped: list = field(default_factory=list)
    undo: str | None = None
    # The cluster the faces went to, when the batch started one.
    cluster: object = None


@dataclass(frozen=True)
class Target:
    """Where an ``assign`` sends its faces: exactly one of the four."""

    cluster: object = None
    person: object = None
    new_person: str = ""
    new_cluster: bool = False


def apply(user, action, faces, target=None):
    """Apply *action* to each of *faces* (the user's, already checked)."""
    result = BatchResult()
    applicable = []
    for face in faces:
        if FaceActionRegistry.is_action_available(action, user, face):
            applicable.append(face)
        else:
            result.skipped.append((face.pk, UNAVAILABLE))
    if not applicable:
        return result
    result.undo = _save_undo(user, applicable)
    touched = set()
    with transaction.atomic():
        _run(user, action, applicable, target, result, touched)
    refresh_clusters(touched)
    if not result.done:
        cache.delete(_UNDO_KEY.format(user.pk, result.undo))
        result.undo = None
    return result


def _run(user, action, faces, target, result, touched):
    if action == ASSIGN:
        _assign(user, faces, target, result, touched)
        return
    for face in faces:
        if action == CONFIRM:
            _attempt(
                result,
                face,
                face_corrections.confirm_face,
                face,
                face.cluster,
                touched=touched,
            )
        elif action == REJECT:
            face_corrections.reject_face(face, touched=touched)
            result.done.append(face.pk)
        elif action == HIDE:
            face_corrections.hide_face(face, touched=touched)
            result.done.append(face.pk)
        elif action == UNHIDE:
            face_corrections.unhide_face(face)
            result.done.append(face.pk)


def _attempt(result, face, fn, *args, **kwargs):
    """Run one face's correction; a refusal skips the face, not the batch."""
    try:
        with transaction.atomic():
            outcome = fn(*args, **kwargs)
    except CorrectionError:
        result.skipped.append((face.pk, ALREADY_IN_PHOTO))
        return None
    result.done.append(face.pk)
    return outcome


def _assign(user, faces, target, result, touched):
    if target.cluster is not None:
        for face in faces:
            _attempt(
                result,
                face,
                face_corrections.confirm_face,
                face,
                target.cluster,
                touched=touched,
            )
        return
    if target.new_cluster:
        for face in faces:
            if result.cluster is None:
                result.cluster = _attempt(
                    result, face, face_corrections.start_cluster, face, touched=touched
                )
            else:
                _attempt(
                    result,
                    face,
                    face_corrections.confirm_face,
                    face,
                    result.cluster,
                    touched=touched,
                )
        return
    person = target.person
    if target.new_person:
        person = create_person(owner=user, display_name=target.new_person)
    known = set(face_people.person_clusters(user, person).values_list("pk", flat=True))
    new_look = None
    for face in faces:
        cluster = _attempt(
            result,
            face,
            face_people.assign_face_to_person,
            face,
            person,
            new_look=new_look,
            touched=touched,
        )
        if cluster is not None and cluster.pk not in known:
            new_look = cluster


# -- undo --------------------------------------------------------------------


def _save_undo(user, faces):
    """Save how *faces* and their clusters are now; return the token."""
    cluster_ids = {f.cluster_id for f in faces} | {f.rejected_cluster_id for f in faces}
    clusters = [
        {
            "uuid": str(c.pk),
            "person": str(c.person_id) if c.person_id else None,
            "hidden": c.hidden,
            "cover": str(c.cover_id) if c.cover_id else None,
            "cover_chosen_at": c.cover_chosen_at,
        }
        for c in FaceCluster.objects.filter(pk__in=cluster_ids - {None})
    ]
    state = {
        "faces": [
            {
                "uuid": str(f.pk),
                "cluster": str(f.cluster_id) if f.cluster_id else None,
                "assignment": f.assignment,
                "rejected_cluster": (
                    str(f.rejected_cluster_id) if f.rejected_cluster_id else None
                ),
            }
            for f in faces
        ],
        "clusters": clusters,
    }
    token = secrets.token_urlsafe(16)
    cache.set(_UNDO_KEY.format(user.pk, token), state, UNDO_TTL)
    return token


def undo(user, token):
    """Put the faces of the batch *token* names back as they were.

    Returns how many faces went back. A face that can no longer go back (its
    photo gained another face of that person since) stays where it is.
    """
    key = _UNDO_KEY.format(user.pk, token)
    state = cache.get(key)
    if state is None:
        raise UndoExpired
    cache.delete(key)
    saved = {row["uuid"]: row for row in state["faces"]}
    faces = list(Face.objects.filter(owner=user, pk__in=list(saved)))
    touched = {f.cluster_id for f in faces} - {None}
    restored = 0
    with transaction.atomic():
        _recreate_clusters(user, state["clusters"])
        # Every face leaves first: two faces of the batch swapping places
        # would otherwise trip the one-face-per-photo constraint midway.
        Face.objects.filter(pk__in=[f.pk for f in faces]).update(cluster=None)
        for face in faces:
            row = saved[str(face.pk)]
            face.cluster_id = row["cluster"]
            face.assignment = row["assignment"]
            face.rejected_cluster_id = row["rejected_cluster"]
            try:
                with transaction.atomic():
                    if face.cluster_id is not None and _person_in_photo(face):
                        raise PhotoAlreadyInCluster
                    face.save(
                        update_fields=["cluster", "assignment", "rejected_cluster"]
                    )
            except IntegrityError, PhotoAlreadyInCluster:
                continue
            restored += 1
        _restore_covers(state["clusters"])
    touched |= {row["uuid"] for row in state["clusters"]}
    refresh_clusters(touched)
    return restored


def _person_in_photo(face):
    return face.cluster_id in {
        str(pk) for pk in photo_clusters(face.file_id, exclude_face=face.pk)
    }


def _recreate_clusters(user, clusters):
    """Bring back, under their own uuid, the clusters the batch emptied."""
    existing = {
        str(pk)
        for pk in FaceCluster.objects.filter(
            pk__in=[c["uuid"] for c in clusters]
        ).values_list("pk", flat=True)
    }
    persons = {
        str(pk)
        for pk in Person.objects.filter(
            pk__in=[c["person"] for c in clusters if c["person"]]
        ).values_list("pk", flat=True)
    }
    for row in clusters:
        if row["uuid"] in existing:
            continue
        FaceCluster.objects.create(
            uuid=row["uuid"],
            owner=user,
            person_id=row["person"] if row["person"] in persons else None,
            hidden=row["hidden"],
        )


def _restore_covers(clusters):
    """Give each cluster back the cover it had, when that face is in it again."""
    for row in clusters:
        if row["cover"] is None:
            continue
        if Face.objects.filter(pk=row["cover"], cluster_id=row["uuid"]).exists():
            FaceCluster.objects.filter(pk=row["uuid"]).update(
                cover_id=row["cover"], cover_chosen_at=row["cover_chosen_at"]
            )

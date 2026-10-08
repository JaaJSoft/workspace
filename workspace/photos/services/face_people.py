"""Naming face clusters after people from the People module.

``photos`` depends on ``people`` and never the reverse: a ``Person`` knows
nothing about faces. Several clusters may be one person (the same face over
the years); in a photo they count as one, a face named after someone already
in it being folded into theirs (see face_corrections.fold_face).
"""

from dataclasses import dataclass

import numpy as np
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from PIL import Image

from workspace.common.vectors.encoding import from_bytes
from workspace.people.services.avatar import save_avatar
from workspace.people.services.persons import create_person

from ..indexes import FACE_EMBEDDINGS
from ..models import Face, FaceCluster
from .face_corrections import (
    CorrectionError,
    confirm_face,
    fold_face,
    start_cluster,
)
from .face_grouping import max_distance, photo_clusters, refresh_clusters


class NoCover(CorrectionError):
    """The cluster has no face picture to offer as an avatar."""


def already_named_faces(cluster, person):
    """The faces of *cluster* in a photo where a face of another cluster of
    *person* already is."""
    others = FaceCluster.objects.filter(person=person).exclude(pk=cluster.pk)
    return Face.objects.filter(
        cluster=cluster,
        file_id__in=Face.objects.filter(cluster__in=others).values("file_id"),
    )


def link_cluster(cluster, person):
    """Name *cluster* after *person*, or clear its name with None.

    Its faces of a photo where the person already is are folded into the
    face already them.
    """
    seen = list(already_named_faces(cluster, person)) if person is not None else []
    touched = set()
    with transaction.atomic():
        for face in seen:
            fold_face(face, touched=touched)
        if person is not None:
            _pin_person_cover(cluster, person)
        cluster.person = person
        cluster.save(update_fields=["person", "updated_at"])
    refresh_clusters(touched)


def _pin_person_cover(cluster, person):
    """Keep the face *person*'s card shows as *cluster* joins them.

    Unless the user picked one, a card shows its largest cluster's cover,
    which the newcomer may be: the cover shown now is pinned instead.
    """
    from ..queries import user_face_clusters

    others = list(
        user_face_clusters(cluster.owner).filter(person=person).exclude(pk=cluster.pk)
    )
    if not others or any(c.cover_chosen_at is not None for c in others):
        return
    shown = person_cards(others)[0].cover_cluster
    if shown.cover_id is not None:
        FaceCluster.objects.filter(pk=shown.pk).update(cover_chosen_at=timezone.now())


def link_new_person(cluster, user, name):
    """Name *cluster* after a new personal contact called *name*."""
    with transaction.atomic():
        person = create_person(owner=user, display_name=name)
        link_cluster(cluster, person)
    return person


def unlink_person(user, person):
    """Clear *person*'s name from every cluster of *user*'s."""
    FaceCluster.objects.filter(owner=user, person=person).update(person=None)


def person_clusters(user, person):
    return FaceCluster.objects.filter(owner=user, person=person)


def person_faces_in_photo(face, person):
    """The other faces of *face*'s photo already named *person*."""
    return Face.objects.filter(file_id=face.file_id, cluster__person=person).exclude(
        pk=face.pk
    )


@transaction.atomic
def assign_face_to_person(face, person, *, new_look=None, touched=None):
    """This is *person*: pin *face* in the person's closest cluster.

    The closest by centroid among those the photo does not rule out; a new
    cluster of the person when none is near enough, the new look of someone
    (a beard, twenty years on) being the usual reason. *new_look*, a cluster
    of the person started earlier in the same batch, takes the face instead
    of yet another new one. Returns the cluster.

    In a photo already showing *person*, *face* is folded into the face
    that is, and the cluster returned is that face's.
    """
    shown = person_faces_in_photo(face, person).select_related("cluster").first()
    if shown is not None:
        fold_face(face, touched=touched)
        return shown.cluster
    taken = photo_clusters(face.file_id, exclude_face=face.pk)
    candidates = [
        cluster
        for cluster in FaceCluster.objects.filter(owner_id=face.owner_id, person=person)
        if cluster.pk not in taken
    ]
    closest = _closest(face, candidates)
    if closest is None and new_look is not None and new_look.pk not in taken:
        closest = new_look
    if closest is None:
        return start_cluster(face, person, touched=touched)
    confirm_face(face, closest, touched=touched)
    return closest


def _closest(face, clusters):
    vector = (
        from_bytes(face.embedding, FACE_EMBEDDINGS.dims) if face.embedding else None
    )
    if vector is None:
        return None
    best, best_distance = None, max_distance()
    for cluster in clusters:
        centroid = (
            from_bytes(cluster.centroid, FACE_EMBEDDINGS.dims)
            if cluster.centroid
            else None
        )
        if centroid is None:
            continue
        distance = 1 - float(np.dot(vector, centroid))
        if distance <= best_distance:
            best, best_distance = cluster, distance
    return best


def use_cover_as_avatar(cluster):
    """Give the cluster's person its cover face as a contact photo.

    Stored through the People avatar service like any upload, so People
    keeps knowing nothing about faces.
    """
    crop = (
        Face.objects.filter(pk=cluster.cover_id).values_list("crop", flat=True).first()
    )
    if cluster.person is None or not crop:
        raise NoCover
    try:
        handle = default_storage.open(crop, "rb")
    except FileNotFoundError as exc:
        raise NoCover from exc
    with handle:
        with Image.open(handle) as image:
            width, height = image.size
        handle.seek(0)
        save_avatar(cluster.person, handle, 0, 0, width, height)


@dataclass(frozen=True)
class PersonCard:
    """What a listing shows of one named person: their clusters, together."""

    person: object
    clusters: tuple
    # A photo is never in two clusters of one person, so the counts add up.
    photo_count: int
    # The cluster whose cover stands for the person: the one whose cover the
    # user picked last, their largest while they never picked one.
    cover_cluster: object
    hidden: bool


def person_cards(clusters):
    """*clusters* (annotated with ``photo_count``) folded into one card per
    named person, largest first. Unnamed clusters are left out.

    A person is hidden only when every one of their clusters is.
    """
    by_person = {}
    for cluster in clusters:
        if cluster.person_id is not None:
            by_person.setdefault(cluster.person_id, []).append(cluster)
    cards = []
    for group in by_person.values():
        group.sort(key=lambda c: (-c.photo_count, c.created_at))
        chosen = [c for c in group if c.cover_chosen_at is not None]
        cards.append(
            PersonCard(
                person=group[0].person,
                clusters=tuple(group),
                photo_count=sum(c.photo_count for c in group),
                cover_cluster=(
                    max(chosen, key=lambda c: c.cover_chosen_at) if chosen else group[0]
                ),
                hidden=all(c.hidden for c in group),
            )
        )
    cards.sort(key=lambda card: (-card.photo_count, card.person.display_name.lower()))
    return cards

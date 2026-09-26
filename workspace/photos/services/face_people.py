"""Naming face clusters after people from the People module.

``photos`` depends on ``people`` and never the reverse: a ``Person`` knows
nothing about faces. Several clusters may be one person (the same face over
the years); for the one-face-per-photo rule they then count as one.
"""

from dataclasses import dataclass

import numpy as np
from django.core.files.storage import default_storage
from django.db import transaction
from PIL import Image

from workspace.common.vectors.encoding import from_bytes
from workspace.people.services.avatar import save_avatar
from workspace.people.services.persons import create_person

from ..indexes import FACE_EMBEDDINGS
from ..models import Face, FaceCluster
from .face_corrections import (
    CorrectionError,
    PhotoAlreadyInCluster,
    confirm_face,
    start_cluster,
)
from .face_grouping import max_distance, photo_clusters


class PersonAlreadyInPhoto(CorrectionError):
    """Naming the cluster would put one person twice in some photo."""


class NoCover(CorrectionError):
    """The cluster has no face picture to offer as an avatar."""


def _shares_a_photo(cluster, person):
    """Whether *cluster* has a face in a photo another cluster of *person* is in."""
    others = FaceCluster.objects.filter(person=person).exclude(pk=cluster.pk)
    files = Face.objects.filter(cluster__in=others).values("file_id")
    return Face.objects.filter(cluster=cluster, file_id__in=files).exists()


def link_cluster(cluster, person):
    """Name *cluster* after *person*, or clear its name with None."""
    if person is not None and _shares_a_photo(cluster, person):
        raise PersonAlreadyInPhoto
    cluster.person = person
    cluster.save(update_fields=["person", "updated_at"])


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


def assign_face_to_person(face, person, *, new_look=None, touched=None):
    """This is *person*: pin *face* in the person's closest cluster.

    The closest by centroid among those the photo does not rule out; a new
    cluster of the person when none is near enough, the new look of someone
    (a beard, twenty years on) being the usual reason. *new_look*, a cluster
    of the person started earlier in the same batch, takes the face instead
    of yet another new one. Returns the cluster.
    """
    taken = photo_clusters(face.file_id, exclude_face=face.pk)
    candidates = [
        cluster
        for cluster in FaceCluster.objects.filter(owner_id=face.owner_id, person=person)
        if cluster.pk not in taken
    ]
    if (
        not candidates
        and FaceCluster.objects.filter(
            owner_id=face.owner_id, person=person, pk__in=taken
        ).exists()
    ):
        raise PersonAlreadyInPhoto
    closest = _closest(face, candidates)
    if closest is None and new_look is not None and new_look.pk not in taken:
        closest = new_look
    if closest is None:
        try:
            return start_cluster(face, person, touched=touched)
        except PhotoAlreadyInCluster as exc:
            raise PersonAlreadyInPhoto from exc
    try:
        confirm_face(face, closest, touched=touched)
    except PhotoAlreadyInCluster as exc:
        raise PersonAlreadyInPhoto from exc
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

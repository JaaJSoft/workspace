"""What is left for the user to settle in their face grouping.

Three queues, for the review page:

- **To name**: the unnamed clusters, each with a few of its faces and, when
  one is close enough, the named person it most looks like. Clusters are
  never merged automatically, so the same person in a new look ends up here.
- **To check**: faces the grouping put in a named person's cluster that the
  user never confirmed, and that sit far from the cluster's centroid.
- **Unassigned**: faces in no cluster - taken out of one by the user, or
  never grouped - waiting for someone to say who they are.

And the faces the user hid, for the Hidden page.
"""

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from workspace.common.vectors.encoding import from_bytes

from ..indexes import FACE_EMBEDDINGS
from ..models import Face
from ..queries import user_face_clusters, user_faces
from .face_grouping import LOW_QUALITY, max_distance
from .face_people import person_cards

# A face further than this fraction of the grouping threshold from its
# cluster's centroid is worth a look. The centroid being a mean, a face of
# the right person usually sits well inside the threshold.
DOUBT_RATIO = 0.5
# Faces shown next to the cover of an unnamed cluster.
SAMPLE_FACES = 5
# Faces to check shown per person at once; the rest come after a reload.
DOUBTS_PER_PERSON = 60
# Unassigned or hidden faces shown at once; the rest come after a reload.
FACES_PER_PAGE = 200

# The two halves of the unassigned queue.
REJECTED = "rejected"
UNGROUPED = "ungrouped"


@dataclass(frozen=True)
class UnnamedCluster:
    cluster: object
    sample_face_ids: tuple
    # The PersonCard of the named person the cluster looks like, or None.
    suggestion: object


@dataclass(frozen=True)
class DoubtfulFace:
    face_id: object
    cluster_id: object
    distance: float


@dataclass(frozen=True)
class FacePage:
    face_ids: tuple
    total: int


@dataclass(frozen=True)
class PersonDoubts:
    card: object
    # Most doubtful first, at most DOUBTS_PER_PERSON.
    faces: tuple
    total: int


def _unit(blob):
    return from_bytes(blob, FACE_EMBEDDINGS.dims) if blob else None


def unnamed_queue(user):
    """The user's visible unnamed clusters, largest first."""
    clusters = list(
        user_face_clusters(user)
        .filter(person__isnull=True, hidden=False, photo_count__gt=0)
        .order_by("-photo_count", "-face_count", "created_at")
    )
    if not clusters:
        return []
    faces = defaultdict(list)
    files = defaultdict(set)
    for pk, cluster_id, file_id in (
        user_faces(user)
        .filter(cluster__in=[c.pk for c in clusters])
        .order_by("-quality")
        .values_list("pk", "cluster_id", "file_id")
    ):
        faces[cluster_id].append(pk)
        files[cluster_id].add(file_id)
    suggest = _suggester(user)
    return [
        UnnamedCluster(
            cluster=cluster,
            sample_face_ids=tuple(
                pk for pk in faces[cluster.pk] if pk != cluster.cover_id
            )[:SAMPLE_FACES],
            suggestion=suggest(cluster, files[cluster.pk]),
        )
        for cluster in clusters
    ]


def _suggester(user):
    """A function giving the named person an unnamed cluster looks like.

    The nearest named cluster by centroid, within the grouping threshold,
    whose person is in none of the cluster's photos: naming it after them
    would be refused.
    """
    clusters = list(
        user_face_clusters(user)
        .filter(person__isnull=False, photo_count__gt=0)
        .select_related("person")
    )
    # A centroid of another size (a backend switch not rebuilt yet) has no
    # direction to compare.
    decoded = [
        (cluster, vector)
        for cluster in clusters
        if (vector := _unit(cluster.centroid)) is not None
    ]
    if not decoded:
        return lambda cluster, files: None
    named = [cluster for cluster, _ in decoded]
    centroids = np.stack([vector for _, vector in decoded])
    cards = {card.person.pk: card for card in person_cards(clusters)}
    person_files = defaultdict(set)
    for person_id, file_id in Face.objects.filter(
        cluster__in=[c.pk for c in clusters]
    ).values_list("cluster__person_id", "file_id"):
        person_files[person_id].add(file_id)
    threshold = max_distance()

    def suggest(cluster, files):
        vector = _unit(cluster.centroid)
        if vector is None:
            return None
        distances = 1 - centroids @ vector
        for index in np.argsort(distances):
            if distances[index] > threshold:
                return None
            person_id = named[index].person_id
            if not person_files[person_id] & files:
                return cards[person_id]
        return None

    return suggest


def doubtful_faces(user):
    """Unconfirmed faces far from their named cluster's centroid, by person.

    People come largest first, their faces most doubtful first.
    """
    clusters = {
        cluster.pk: cluster
        for cluster in user_face_clusters(user)
        .filter(person__isnull=False, hidden=False, photo_count__gt=0)
        .select_related("person")
    }
    centroids = {pk: _unit(c.centroid) for pk, c in clusters.items()}
    limit = max_distance() * DOUBT_RATIO
    by_person = defaultdict(list)
    for pk, cluster_id, blob in (
        user_faces(user)
        .filter(cluster__in=list(clusters), assignment=Face.Assignment.AUTO)
        .values_list("pk", "cluster_id", "embedding")
    ):
        vector, centroid = _unit(blob), centroids[cluster_id]
        if vector is None or centroid is None:
            continue
        distance = 1 - float(np.dot(vector, centroid))
        if distance > limit:
            by_person[clusters[cluster_id].person_id].append(
                DoubtfulFace(face_id=pk, cluster_id=cluster_id, distance=distance)
            )
    result = []
    for card in person_cards(clusters.values()):
        doubts = sorted(by_person.get(card.person.pk, ()), key=lambda d: -d.distance)
        if doubts:
            result.append(
                PersonDoubts(
                    card=card,
                    faces=tuple(doubts[:DOUBTS_PER_PERSON]),
                    total=len(doubts),
                )
            )
    return result


def unassigned_faces(user, kind=REJECTED):
    """Faces in no cluster that still wait for a person.

    *REJECTED*: those the user took out of a cluster. *UNGROUPED*: those the
    grouping never placed, good enough to recognise someone by - the blurred
    crowd behind a subject would bury the rest. The best faces first, then
    chained by likeness so one person's faces sit side by side.
    """
    faces = _unassigned(user, kind)
    rows = faces.order_by("-quality", "pk").values_list("pk", "embedding")
    return FacePage(
        face_ids=tuple(by_likeness(rows[:FACES_PER_PAGE])), total=faces.count()
    )


def unassigned_counts(user):
    """How many faces each half of the unassigned queue holds."""
    return {kind: _unassigned(user, kind).count() for kind in (REJECTED, UNGROUPED)}


def _unassigned(user, kind):
    faces = user_faces(user).filter(cluster__isnull=True)
    if kind == REJECTED:
        return faces.filter(assignment=Face.Assignment.REJECTED)
    return faces.filter(assignment=Face.Assignment.AUTO, quality__gte=LOW_QUALITY)


def hidden_faces(user):
    """The faces the user hid, most recently found first."""
    faces = user_faces(user).filter(assignment=Face.Assignment.HIDDEN)
    ids = faces.order_by("-created_at", "pk").values_list("pk", flat=True)
    return FacePage(face_ids=tuple(ids[:FACES_PER_PAGE]), total=faces.count())


def by_likeness(rows):
    """The ids of *rows*, ``(pk, embedding)`` pairs, chained by likeness.

    Starts from the first row, then each face is followed by the nearest one
    left: look-alikes come side by side. Faces without an embedding go last.
    """
    ids, vectors, rest = [], [], []
    for pk, blob in rows:
        vector = _unit(blob)
        if vector is None:
            rest.append(pk)
        else:
            ids.append(pk)
            vectors.append(vector)
    if not ids:
        return rest
    matrix = np.stack(vectors)
    left = np.ones(len(ids), dtype=bool)
    order = [0]
    left[0] = False
    for _ in range(len(ids) - 1):
        distances = 1 - matrix @ matrix[order[-1]]
        distances[~left] = np.inf
        following = int(np.argmin(distances))
        order.append(following)
        left[following] = False
    return [ids[i] for i in order] + rest

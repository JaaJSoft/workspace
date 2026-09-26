"""Group one user's faces into clusters.

Two passes, both confined to one owner's faces:

- **Assignment**, right after a photo is analyzed: each new face asks the
  vector index for its nearest neighbours and joins the cluster they vote
  for, if they are close enough.
- **Clustering**, nightly or once enough faces wait: DBSCAN over the faces
  still ungrouped, creating new clusters.

What the user decided is never undone: a confirmed face stays where it was
put, a rejected one is never grouped again, and no face is proposed for a
cluster that already holds another face of its photo (the database refuses it
anyway).
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction

from workspace.common.vectors import nearest
from workspace.common.vectors.encoding import from_bytes, normalize, to_bytes

from ..indexes import FACE_EMBEDDINGS
from ..models import Face, FaceCluster
from .detection.registry import get_face_backend

logger = logging.getLogger(__name__)

# Under this quality a face never seeds a cluster; it joins one only through
# a confident neighbour.
LOW_QUALITY = 0.5
# Neighbours asked for when assigning a face.
_NEIGHBOURS = 16
# A neighbour this much closer than the threshold counts as confident.
_CONFIDENT = 0.8
# A low-quality face weighs this little in a centroid, never nothing.
_MIN_WEIGHT = 0.1
# DBSCAN: a core face has at least this many faces (itself included) within
# the threshold. Two: two photos of one person make a cluster.
_MIN_SAMPLES = 2
# A face DBSCAN left alone becomes a cluster of its own from this quality up:
# one clear photo of someone is enough to find them, while the blurred
# stranger in the background stays out of the list.
SINGLETON_QUALITY = 0.7
# Rows of the distance matrix computed at once, and neighbours kept per face:
# the full matrix of a large backlog would not fit in memory.
_BLOCK = 512
_MAX_LINKS = 64
# The most ungrouped faces one clustering run reads, the best first.
_MAX_CLUSTERED = 20000

_QUEUED_KEY = "photos:faces:clustering-queued:{}"
_RUNNING_KEY = "photos:faces:clustering-running:{}"
_QUEUED_TTL = 10 * 60
_RUNNING_TTL = 30 * 60


def max_distance():
    """Cosine distance under which two faces are taken for one person."""
    configured = settings.PHOTOS_FACES_MAX_DISTANCE
    return (
        configured
        if configured is not None
        else get_face_backend().default_max_distance
    )


def _vector(face):
    return from_bytes(face.embedding, FACE_EMBEDDINGS.dims) if face.embedding else None


def _weight(quality):
    return max(float(quality), _MIN_WEIGHT)


# -- assignment --------------------------------------------------------------


def assign_faces(owner_id, face_ids):
    """Put each of *face_ids* in the cluster its neighbours vote for.

    Only ungrouped automatic faces move. Returns the ids of the clusters
    that gained a face.
    """
    threshold = max_distance()
    faces = Face.objects.filter(
        pk__in=face_ids,
        owner_id=owner_id,
        cluster__isnull=True,
        assignment=Face.Assignment.AUTO,
        embedding__isnull=False,
    ).order_by("-quality")
    touched = set()
    for face in faces:
        cluster_id = _vote(face, threshold)
        if cluster_id is not None and _place(face.pk, cluster_id):
            touched.add(cluster_id)
    refresh_clusters(touched)
    return touched


def _vote(face, threshold):
    """The cluster *face*'s neighbours vote for, or None."""
    vector = _vector(face)
    if vector is None:
        return None
    hits = nearest(FACE_EMBEDDINGS, vector, partition=face.owner_id, k=_NEIGHBOURS)
    distances = {pk: d for pk, d in hits if d <= threshold and pk != face.pk}
    if not distances:
        return None
    neighbours = (
        Face.objects.filter(
            pk__in=distances, owner_id=face.owner_id, cluster__isnull=False
        )
        .exclude(file_id=face.file_id)
        .values_list("pk", "cluster_id", "quality")
    )
    excluded = set(
        Face.objects.filter(file_id=face.file_id, cluster__isnull=False).values_list(
            "cluster_id", flat=True
        )
    )
    if face.rejected_cluster_id:
        excluded.add(face.rejected_cluster_id)
    scores = defaultdict(float)
    confident = set()
    for pk, cluster_id, quality in neighbours:
        if cluster_id in excluded:
            continue
        distance = distances[pk]
        scores[cluster_id] += (1 - distance / threshold + 1e-6) * _weight(quality)
        if quality >= LOW_QUALITY and distance <= threshold * _CONFIDENT:
            confident.add(cluster_id)
    if face.quality < LOW_QUALITY:
        scores = {cid: score for cid, score in scores.items() if cid in confident}
    if not scores:
        return None
    return max(scores, key=scores.get)


def _place(face_id, cluster_id):
    """Move an ungrouped automatic face into *cluster_id*; False if it could not.

    Conditional: a face grouped or corrected in the meantime stays as it is,
    and the one-face-per-photo constraint turns a race into a refusal.
    """
    try:
        with transaction.atomic():
            return bool(
                Face.objects.filter(
                    pk=face_id, cluster__isnull=True, assignment=Face.Assignment.AUTO
                ).update(cluster_id=cluster_id)
            )
    except IntegrityError:
        return False


def pending_count(owner_id):
    """Ungrouped faces good enough to seed a cluster."""
    return Face.objects.filter(
        owner_id=owner_id,
        cluster__isnull=True,
        assignment=Face.Assignment.AUTO,
        quality__gte=LOW_QUALITY,
        embedding__isnull=False,
    ).count()


def maybe_queue_clustering(owner_id):
    """Queue a clustering run when enough faces wait, at most once per window."""
    if pending_count(owner_id) < settings.PHOTOS_FACES_CLUSTER_PENDING:
        return False
    if not cache.add(_QUEUED_KEY.format(owner_id), True, _QUEUED_TTL):
        return False
    from ..tasks import cluster_faces

    transaction.on_commit(lambda: cluster_faces.delay(owner_id))
    return True


# -- clustering --------------------------------------------------------------


def cluster_owner(owner_id):
    """Group *owner_id*'s ungrouped faces; return the number of new clusters.

    First every ungrouped face gets another chance to join an existing
    cluster, then DBSCAN runs over what is left. One run per owner at a time:
    two would create the same clusters twice.
    """
    cache.delete(_QUEUED_KEY.format(owner_id))
    running = _RUNNING_KEY.format(owner_id)
    if not cache.add(running, True, _RUNNING_TTL):
        return 0
    try:
        ungrouped = Face.objects.filter(
            owner_id=owner_id,
            cluster__isnull=True,
            assignment=Face.Assignment.AUTO,
            embedding__isnull=False,
        )
        assign_faces(owner_id, list(ungrouped.values_list("pk", flat=True)))
        created = _cluster_remaining(owner_id, ungrouped)
        refresh_clusters(
            FaceCluster.objects.filter(owner_id=owner_id).values_list("pk", flat=True)
        )
        return created
    finally:
        cache.delete(running)


def _cluster_remaining(owner_id, ungrouped):
    rows = list(
        ungrouped.order_by("-quality").values_list(
            "pk", "file_id", "quality", "embedding"
        )[:_MAX_CLUSTERED]
    )
    ids, files, qualities, vectors = [], [], [], []
    for pk, file_id, quality, blob in rows:
        vector = from_bytes(blob, FACE_EMBEDDINGS.dims)
        if vector is None:
            continue
        ids.append(pk)
        files.append(file_id)
        qualities.append(quality)
        vectors.append(vector)
    if not ids:
        return 0
    vectors = np.stack(vectors).astype(np.float32)
    qualities = np.asarray(qualities, dtype=np.float32)
    threshold = max_distance()
    labels = dbscan(vectors, qualities, threshold)
    groups = []
    for label in range(labels.max() + 1):
        members = np.nonzero(labels == label)[0]
        if len({files[i] for i in members}) < len(members):
            groups += split_by_photo(members, files, vectors, qualities, threshold)
        else:
            groups.append(list(members))
    grouped = {i for group in groups if len(group) >= _MIN_SAMPLES for i in group}
    groups = [group for group in groups if len(group) >= _MIN_SAMPLES] + [
        [i]
        for i in range(len(ids))
        if i not in grouped and qualities[i] >= SINGLETON_QUALITY
    ]
    created = 0
    for group in groups:
        if _create_cluster(owner_id, [ids[i] for i in group]):
            created += 1
    return created


def dbscan(vectors, qualities, threshold):
    """Cluster labels for unit *vectors* (-1: noise), by cosine distance.

    Only faces of good quality can be core points, so a blurred face can
    join a cluster but never bridge two. Each face keeps its _MAX_LINKS
    nearest neighbours only, which bounds memory on a large backlog.
    """
    count = len(vectors)
    good = qualities >= LOW_QUALITY
    links = []
    is_core = np.zeros(count, dtype=bool)
    for start in range(0, count, _BLOCK):
        distances = 1 - vectors[start : start + _BLOCK] @ vectors.T
        for offset, row in enumerate(distances):
            close = np.nonzero(row <= threshold)[0]
            is_core[start + offset] = (
                good[start + offset] and good[close].sum() >= _MIN_SAMPLES
            )
            if len(close) > _MAX_LINKS:
                close = close[np.argpartition(row[close], _MAX_LINKS)[:_MAX_LINKS]]
            links.append(close)
    labels = np.full(count, -1)
    label = 0
    for seed in range(count):
        if labels[seed] != -1 or not is_core[seed]:
            continue
        labels[seed] = label
        stack = [seed]
        while stack:
            point = stack.pop()
            for other in links[point]:
                if labels[other] == -1:
                    labels[other] = label
                    if is_core[other]:
                        stack.append(other)
        label += 1
    return labels


def split_by_photo(members, files, vectors, qualities, threshold):
    """Split a group holding two faces of one photo into groups that do not.

    Greedy, best faces first: each face joins the nearest sub-group within
    the threshold that has no face of its photo yet, or starts a new one if
    it is good enough to seed it.
    """
    groups = []
    for i in sorted(members, key=lambda m: -qualities[m]):
        best, best_distance = None, threshold
        for group in groups:
            if files[i] in group["files"]:
                continue
            centroid = group["sum"] / np.linalg.norm(group["sum"])
            distance = 1 - float(vectors[i] @ centroid)
            if distance <= best_distance:
                best, best_distance = group, distance
        if best is None:
            if qualities[i] < LOW_QUALITY:
                continue
            best = {"members": [], "files": set(), "sum": np.zeros_like(vectors[i])}
            groups.append(best)
        best["members"].append(i)
        best["files"].add(files[i])
        best["sum"] = best["sum"] + vectors[i] * _weight(qualities[i])
    return [group["members"] for group in groups]


def _create_cluster(owner_id, face_ids):
    with transaction.atomic():
        cluster = FaceCluster.objects.create(owner_id=owner_id)
        placed = [pk for pk in face_ids if _place(pk, cluster.pk)]
        if not placed:
            Face.objects.filter(cluster=cluster).update(cluster=None)
            cluster.delete()
            return None
    refresh_clusters([cluster.pk])
    return cluster


# -- bookkeeping -------------------------------------------------------------


def refresh_clusters(cluster_ids):
    """Recompute the centroid, count and cover of each cluster; drop empty ones."""
    for cluster in FaceCluster.objects.filter(pk__in=list(cluster_ids)):
        refresh_cluster(cluster)


def refresh_cluster(cluster):
    members = list(
        Face.objects.filter(cluster=cluster).values_list(
            "pk", "quality", "embedding", "file__deleted_at"
        )
    )
    if not members:
        cluster.delete()
        return None
    total = None
    for _pk, quality, blob, _deleted in members:
        vector = from_bytes(blob, FACE_EMBEDDINGS.dims) if blob else None
        if vector is not None:
            weighted = vector.astype(np.float64) * _weight(quality)
            total = weighted if total is None else total + weighted
    try:
        cluster.centroid = (
            to_bytes(normalize(total, FACE_EMBEDDINGS.dims))
            if total is not None
            else None
        )
    except ValueError:
        cluster.centroid = None
    cluster.face_count = len(members)
    member_ids = {pk for pk, *_ in members}
    if cluster.cover_id not in member_ids:
        # The best face whose photo is not in the trash, if there is one.
        best = max(members, key=lambda m: (m[3] is None, m[1]))
        cluster.cover_id = best[0]
    cluster.save(update_fields=["centroid", "face_count", "cover", "updated_at"])
    return cluster

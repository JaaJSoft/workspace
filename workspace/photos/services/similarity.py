"""Similar photos, near-duplicates and search by description, over the
embeddings of the photos and of the videos' key frames
(services/photo_embeddings.py).

A video ranks by its closest key frame and is listed once, with the time of
that frame: a result opens it at the moment that matched. A video asking for
its look-alikes asks with every one of its key frames.

The vector index narrows the search to one owner's files at a time; it
authorizes nothing. Every answer re-enters the library through
``queries.library_files(user, scope)``, so a file the user cannot open, or
that is trashed, quarantined or hidden, never surfaces through a vector.
"""

from __future__ import annotations

from dataclasses import dataclass

from workspace.common.vectors import nearest
from workspace.common.vectors.encoding import from_bytes
from workspace.common.vectors.schema import MAX_K

from ..indexes import PHOTO_EMBEDDINGS
from ..models import PhotoEmbedding
from ..queries import MINE, library_files
from .embedding.registry import (
    description_max_distance,
    duplicate_max_distance,
    get_embedding_backend,
    similar_max_distance,
)
from .photo_embeddings import embed_query

# The most photos one similarity or description search returns. A ranking,
# not a listing: past the first screens the matches are no longer close.
RESULT_LIMIT = 120
# The most merely similar photos shown after the near-duplicates, about a
# screen. A library of events shot in bursts has a hundred look-alikes under
# the threshold for nearly every photo; past the first screen they share the
# setting, not the subject.
SIMILAR_LIMIT = 30


@dataclass(frozen=True)
class Match:
    file_id: object
    distance: float
    near_duplicate: bool = False
    # Seconds into a video of the key frame that matched, and the row whose
    # picture shows it; None for a photo.
    timestamp: float | None = None
    frame_id: object = None


def _embeddings(files, model_key):
    return PhotoEmbedding.objects.filter(
        file_id__in=files.values("pk"),
        model=model_key,
        embedding__isnull=False,
    )


def _rank(user, scope, vectors, *, max_distance, limit, exclude=()):
    """The files of the library in *scope* within *max_distance* of any of
    *vectors*, nearest first, each once, as Match.

    One KNN per owner whose files are in scope and per vector: the user
    alone for their own library, the members of a group, whoever shared
    photos with them.
    """
    files = library_files(user, scope)
    model_key = get_embedding_backend().model_key
    embeddings = _embeddings(files, model_key)
    # Each partition is asked for more than the limit: a video holds a row
    # per key frame, and the hits that do not make it back through the
    # library (another model's vector, a file trashed since, someone else's
    # photo in a shared owner's partition) are dropped below.
    k = min(MAX_K, max(8 * limit, 400))
    closest = {}
    for owner_id in embeddings.values_list("owner_id", flat=True).distinct():
        for vector in vectors:
            for pk, distance in nearest(
                PHOTO_EMBEDDINGS, vector, partition=owner_id, k=k
            ):
                if distance <= max_distance:
                    closest[pk] = min(distance, closest.get(pk, distance))
    if not closest:
        return []
    rows = {
        pk: (file_id, timestamp, frame)
        for pk, file_id, timestamp, frame in embeddings.filter(
            pk__in=list(closest)
        ).values_list("pk", "file_id", "timestamp", "frame")
    }
    matches, seen = [], set(exclude)
    for pk, distance in sorted(closest.items(), key=lambda hit: hit[1]):
        if pk not in rows:
            continue
        file_id, timestamp, frame = rows[pk]
        if file_id in seen:
            continue
        seen.add(file_id)
        matches.append(
            Match(
                file_id=file_id,
                distance=distance,
                timestamp=timestamp,
                frame_id=pk if frame else None,
            )
        )
        if len(matches) == limit:
            break
    return matches


def file_vectors(file_obj):
    """*file_obj*'s embeddings under the configured model: one for a photo,
    one per key frame for a video, none when it has none yet (not analyzed,
    or unreadable)."""
    return [
        from_bytes(embedding, PHOTO_EMBEDDINGS.dims)
        for embedding in PhotoEmbedding.objects.filter(
            file_id=file_obj.pk,
            model=get_embedding_backend().model_key,
            embedding__isnull=False,
        ).values_list("embedding", flat=True)
    ]


def similar_photos(user, source, scope=MINE, *, limit=RESULT_LIMIT):
    """The photos and videos of *scope* that look like *source*, nearest
    first: up to *limit* near-duplicates, flagged, then up to SIMILAR_LIMIT
    similar ones. None when *source* has no embedding yet.

    *source* is a file the caller checked the user can open. A video is
    never similar to itself, and one with several close frames is listed
    once, by the closest.
    """
    vectors = file_vectors(source)
    if not vectors:
        return None
    duplicate = duplicate_max_distance()
    matches = [
        Match(
            file_id=match.file_id,
            distance=match.distance,
            near_duplicate=match.distance <= duplicate,
            timestamp=match.timestamp,
            frame_id=match.frame_id,
        )
        for match in _rank(
            user,
            scope,
            vectors,
            max_distance=similar_max_distance(),
            # Room for both groups: a long burst alone could fill *limit*.
            limit=limit + SIMILAR_LIMIT,
            exclude=(source.pk,),
        )
    ]
    near_duplicates = [match for match in matches if match.near_duplicate]
    similar = [match for match in matches if not match.near_duplicate]
    return near_duplicates[:limit] + similar[:SIMILAR_LIMIT]


def search_by_description(user, text, scope=MINE, *, limit=RESULT_LIMIT):
    """The photos and videos of *scope* that *text* describes, the best
    match first.

    Raises photo_embeddings.QueryEmbeddingUnavailable when the text could not
    be embedded.
    """
    return _rank(
        user,
        scope,
        [embed_query(text)],
        max_distance=description_max_distance(),
        limit=limit,
    )

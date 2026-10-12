"""Similar photos, near-duplicates and search by description, over the
photos' embeddings (services/photo_embeddings.py).

The vector index narrows the search to one owner's photos at a time; it
authorizes nothing. Every answer re-enters the library through
``queries.library_files(user, scope)``, so a photo the user cannot open, or
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


def _embeddings(files, model_key):
    return PhotoEmbedding.objects.filter(
        file_id__in=files.values("pk"),
        model=model_key,
        embedding__isnull=False,
    )


def _rank(user, scope, vector, *, max_distance, limit, exclude=()):
    """The photos of the library in *scope* within *max_distance* of
    *vector*, nearest first, as Match.

    One KNN per owner whose photos are in scope: the user alone for their
    own library, the members of a group, whoever shared photos with them.
    """
    files = library_files(user, scope)
    model_key = get_embedding_backend().model_key
    embeddings = _embeddings(files, model_key)
    # Each partition is asked for more than the limit: the hits that do not
    # make it back through the library (another model's vector, a file
    # trashed since, someone else's photo in a shared owner's partition) are
    # dropped below.
    k = min(MAX_K, max(4 * limit, 200))
    hits = []
    for owner_id in embeddings.values_list("owner_id", flat=True).distinct():
        hits += nearest(PHOTO_EMBEDDINGS, vector, partition=owner_id, k=k)
    hits = [(pk, distance) for pk, distance in hits if distance <= max_distance]
    if not hits:
        return []
    file_of = dict(
        embeddings.filter(pk__in=[pk for pk, _ in hits]).values_list("pk", "file_id")
    )
    matches, seen = [], set(exclude)
    for pk, distance in sorted(hits, key=lambda hit: hit[1]):
        file_id = file_of.get(pk)
        if file_id is None or file_id in seen:
            continue
        seen.add(file_id)
        matches.append(Match(file_id=file_id, distance=distance))
        if len(matches) == limit:
            break
    return matches


def photo_vector(file_obj):
    """*file_obj*'s embedding under the configured model, or None when it has
    none yet (not analyzed, or undecodable)."""
    row = (
        PhotoEmbedding.objects.filter(
            file_id=file_obj.pk,
            model=get_embedding_backend().model_key,
            embedding__isnull=False,
        )
        .only("embedding")
        .first()
    )
    if row is None:
        return None
    return from_bytes(row.embedding, PHOTO_EMBEDDINGS.dims)


def similar_photos(user, source, scope=MINE, *, limit=RESULT_LIMIT):
    """The photos of *scope* that look like *source*, nearest first: up to
    *limit* near-duplicates, flagged, then up to SIMILAR_LIMIT similar
    photos. None when *source* has no embedding yet.

    *source* is a file the caller checked the user can open.
    """
    vector = photo_vector(source)
    if vector is None:
        return None
    duplicate = duplicate_max_distance()
    matches = [
        Match(
            file_id=match.file_id,
            distance=match.distance,
            near_duplicate=match.distance <= duplicate,
        )
        for match in _rank(
            user,
            scope,
            vector,
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
    """The photos of *scope* that *text* describes, the best match first.

    Raises photo_embeddings.QueryEmbeddingUnavailable when the text could not
    be embedded.
    """
    return _rank(
        user,
        scope,
        embed_query(text),
        max_distance=description_max_distance(),
        limit=limit,
    )

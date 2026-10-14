"""Automatic tags: each photo of the library tagged with what it shows.

The tags are the classic ones (tag_vocabulary.CLASSIC_TAGS) the configured
backend recognizes: its ``tag_thresholds`` table, fitted per tag by the
embedding bench, names those it tells apart well enough and how close a
photo's vector must lie to the tag name's. A tag the table leaves out is
never placed by that backend. Each lands as a FileTag of source AUTO on the
owner's own Tag of that name (matched case-insensitively, created on first
use).

A photo is tagged once, after it is embedded (``PhotoEmbedding.auto_tagged_at``,
reset with every new vector): a tag its owner took off stays off.

Only personal photos: a Tag belongs to its owner, and the files API tags
nothing else. Runs in the worker, after the embedding and from the hourly
catch-up; the web process never runs a model.
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np
from django.core.cache import cache
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.vectors.encoding import from_bytes, normalize, to_bytes
from workspace.files.models import File, FileTag
from workspace.files.services.tags import get_or_create_tag

from ..indexes import PHOTO_EMBEDDINGS
from ..models import PhotoEmbedding
from .embedding.registry import (
    embeddings_available,
    get_embedding_backend,
    is_misconfigured,
    tag_thresholds,
)
from .preferences import auto_tags
from .tag_vocabulary import CLASSIC_TAGS

logger = logging.getLogger(__name__)

# The vocabulary's vectors, per model and vocabulary: embedded once.
_VOCABULARY_CACHE_KEY = "photos:auto_tags:vocabulary:{}"


def auto_tags_enabled():
    return embeddings_available() and not is_misconfigured()


def vocabulary():
    """The classic tags the backend recognizes, their name vectors (unit
    length, one row each) and their thresholds. Embedded by the backend in
    this process on a cold cache, which loads its text encoder."""
    backend = get_embedding_backend()
    thresholds = tag_thresholds()
    tags = [tag for tag in CLASSIC_TAGS if tag.text in thresholds]
    if not tags:
        return [], np.empty((0, PHOTO_EMBEDDINGS.dims)), np.empty(0)
    texts = "\n".join(tag.text for tag in tags)
    digest = hashlib.sha256(f"{backend.model_key}\n{texts}".encode()).hexdigest()
    key = _VOCABULARY_CACHE_KEY.format(digest[:32])
    cached = cache.get(key)
    if cached is None:
        cached = [
            to_bytes(normalize(backend.embed_text(tag.text), PHOTO_EMBEDDINGS.dims))
            for tag in tags
        ]
        # No expiry: the key changes with the model or the vocabulary.
        cache.set(key, cached, None)
    vectors = np.stack([from_bytes(blob, PHOTO_EMBEDDINGS.dims) for blob in cached])
    return tags, vectors, np.array([thresholds[tag.text] for tag in tags])


def tags_for(vector):
    """The names of the classic tags *vector* (unit length) shows."""
    tags, vectors, thresholds = vocabulary()
    if not tags:
        return []
    under = (1.0 - vectors @ vector) <= thresholds
    return [tag.name for tag, hit in zip(tags, under, strict=True) if hit]


def tag_photo(row):
    """Give the photo of the PhotoEmbedding *row* its automatic tags, once.

    Returns True when the row is settled: tagged, or nothing to tag (no
    vector, a group photo, trashed, the owner turned the tags off).
    """
    file_obj = row.file
    taggable = (
        row.embedding is not None
        and file_obj.group_id is None
        and file_obj.deleted_at is None
        and file_obj.owner_id == row.owner_id
        and auto_tags(file_obj.owner)
    )
    if taggable:
        names = tags_for(from_bytes(row.embedding, PHOTO_EMBEDDINGS.dims))
        tags = {get_or_create_tag(file_obj.owner, name) for name in names}
        # A tag the file already carries, by hand or not, keeps its row.
        FileTag.objects.bulk_create(
            [
                FileTag(tag=tag, file=file_obj, source=FileTag.Source.AUTO)
                for tag in tags
            ],
            ignore_conflicts=True,
        )
    # Conditional: a new vector written meanwhile reset the stamp, and its
    # photo is tagged again from it.
    PhotoEmbedding.objects.filter(pk=row.pk, content_hash=row.content_hash).update(
        auto_tagged_at=timezone.now()
    )
    return True


def tag_photo_safely(row):
    """tag_photo for a caller whose own work must not fail with it (the
    embedding pipeline): a failure is logged, and the catch-up retries."""
    try:
        return tag_photo(row)
    except Exception:
        logger.exception("Automatic tagging failed for %s", scrub(row.file_id))
        return False


def pending_auto_tags_qs(*, reanalyze=False):
    """The live personal photos embedded with a vector of the current model
    but not tagged yet."""
    qs = File.objects.alive().filter(
        group__isnull=True,
        photo_embedding__embedding__isnull=False,
        photo_embedding__model=get_embedding_backend().model_key,
    )
    if reanalyze:
        return qs
    return qs.filter(photo_embedding__auto_tagged_at__isnull=True)


def refresh_auto_tags(file_obj):
    """The catch-up's processor: tag one pending photo."""
    row = (
        PhotoEmbedding.objects.filter(file_id=file_obj.pk)
        .select_related("file__owner")
        .first()
    )
    return row is not None and tag_photo(row)

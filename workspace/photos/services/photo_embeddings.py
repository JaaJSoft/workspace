"""Embed the photos of the library: one vector per photo, in a space shared
with text (see services/embedding/).

Runs in the Celery worker: after the file's content lands (an upload
processor, see services/handlers.py, queued at low priority) or from the
hourly catch-up (background priority). A photo is decoded once, at the
backend's ``decode_size``. The row and its vector are written in one
transaction, and only if the file still holds the bytes that were read.

Every photo of the library is embedded, a group folder's too: unlike a face,
a whole-picture vector says nothing about who anyone is. Videos are left out
for now. Whoever reads the vectors re-enters the library through
``queries.library_files`` (services/similarity.py), so a trashed, quarantined,
hidden or unshared photo never surfaces through them.
"""

from __future__ import annotations

import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.metrics import safe_counter, safe_histogram
from workspace.common.vectors.encoding import from_bytes
from workspace.common.vectors.indexing import drop_vector, index_vector
from workspace.files.models import File
from workspace.files.services.raster_formats import RASTER_LABELS
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked

from ..indexes import PHOTO_EMBEDDINGS
from ..models import MediaItem, PhotoEmbedding
from .analysis import library_candidates, media_type_for
from .embedding.registry import (
    embeddings_available,
    get_embedding_backend,
    is_misconfigured,
)
from .photo_decoding import decode_photo

logger = logging.getLogger(__name__)

# A query text's vector, per model: paging through results or coming back to
# a search embeds nothing again.
_QUERY_CACHE_KEY = "photos:embedding:query:{model}:{text}"
_QUERY_CACHE_TTL = 24 * 3600
# Longer queries are cut: the text encoder reads 77 tokens anyway.
MAX_QUERY_LENGTH = 300

_DURATION = safe_histogram(
    "workspace_photos_embedding_seconds",
    "Time spent embedding one photo.",
    labels=("backend",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
_RESULTS = safe_counter(
    "workspace_photos_embedding_total",
    "Photos embedded, by outcome.",
    labels=("result",),
)


class QueryEmbeddingUnavailable(RuntimeError):
    """The query text could not be embedded: no worker answered in time, or
    the backend failed."""


def is_embedding_candidate(file_obj):
    """Whether *file_obj* is a photo this instance embeds."""
    return (
        embeddings_available()
        and media_type_for(file_obj) == MediaItem.MediaType.PHOTO
        and file_obj.deleted_at is None
        and not is_blocked(file_obj)
    )


def embeddings_catch_up_enabled():
    """The catch-up only queues work that can run: a misconfigured backend
    shows on the admin dashboard instead of failing every hour."""
    return embeddings_available() and not is_misconfigured()


def pending_embeddings_qs(*, reanalyze=False):
    """The live photos whose embedding is missing or stale.

    Stale: other bytes, another owner (the index partition), or another
    model wrote it. A file whose own hash is empty only counts when it has no
    row at all, as for MediaItem.
    """
    qs = exclude_blocked(
        library_candidates(File.objects.alive().with_blob()).filter(
            type__in=RASTER_LABELS
        )
    )
    if reanalyze:
        return qs
    return qs.filter(
        Q(photo_embedding__isnull=True)
        | ~Q(photo_embedding__owner_id=F("owner_id"))
        | ~Q(photo_embedding__model=get_embedding_backend().model_key)
        | (~Q(content_hash="") & ~Q(photo_embedding__content_hash=F("content_hash")))
    )


def refresh_embedding(file_obj):
    """Embed *file_obj* for the catch-up; True when its row was written."""
    return embed_photo(file_obj) is not None


def embed_photo(file_obj):
    """Embed *file_obj* and store its row; return the row or None.

    None means nothing was stored: the file is not a candidate, its blob
    could not be opened, the backend failed, or the file changed while it
    was read. A photo that cannot be decoded, or is past the size limit, is
    stored without a vector, so it does not come back every hour.
    """
    if not is_embedding_candidate(file_obj):
        return None
    backend = get_embedding_backend()
    model_key = backend.model_key
    existing = PhotoEmbedding.objects.filter(file_id=file_obj.pk).first()
    if (
        existing is not None
        and existing.model == model_key
        and existing.content_hash == file_obj.content_hash
        and file_obj.content_hash
    ):
        # Same bytes, same model: only the partition moved with the file.
        vector = (
            from_bytes(existing.embedding, PHOTO_EMBEDDINGS.dims)
            if existing.embedding
            else None
        )
        return _store(file_obj, model_key, file_obj.content_hash, vector)

    started = time.monotonic()
    try:
        vector = _embed(file_obj, backend)
    except Exception:
        _RESULTS.labels(result="error").inc()
        logger.exception("Photo embedding failed for %s", scrub(file_obj.content.name))
        return None
    if vector is False:
        _RESULTS.labels(result="skipped").inc()
        return None
    _DURATION.labels(backend=backend.key).observe(time.monotonic() - started)
    _RESULTS.labels(result="embedded" if vector is not None else "undecodable").inc()
    return _store(file_obj, model_key, file_obj.content_hash, vector)


def _embed(file_obj, backend):
    """The photo's vector; None when it cannot be decoded or is too large,
    False when its blob could not be opened (the catch-up tries again)."""
    if file_obj.size and file_obj.size > settings.PHOTOS_EMBEDDING_MAX_FILE_BYTES:
        return None
    image = decode_photo(file_obj, backend.decode_size, reader="Photo embedding")
    if image is None or image is False:
        return image
    return backend.embed_image(image)


def _store(file_obj, model_key, read_hash, vector):
    owner_id = file_obj.owner_id
    with transaction.atomic():
        current = (
            File.objects.select_for_update()
            .filter(
                pk=file_obj.pk,
                content_hash=read_hash,
                owner_id=owner_id,
                deleted_at__isnull=True,
            )
            .exists()
        )
        if not current:
            # Moved, replaced or trashed while it was read: whatever changed
            # it queues its own pass, or the catch-up does.
            return None
        row, _ = PhotoEmbedding.objects.update_or_create(
            file_id=file_obj.pk,
            defaults={
                "owner_id": owner_id,
                "content_hash": read_hash,
                "model": model_key,
                "analyzed_at": timezone.now(),
            },
        )
        if vector is None:
            drop_vector(PHOTO_EMBEDDINGS, row.pk)
        else:
            index_vector(PHOTO_EMBEDDINGS, row.pk, vector)
        # Written by the index, normalized: the row in hand holds the old one.
        row.refresh_from_db(fields=["embedding"])
    return row


def forget_embedding(file_obj):
    """Drop the embedding of a file that stopped being a photo."""
    # Instance deletes: the post_delete receiver drops the vector.
    for row in PhotoEmbedding.objects.filter(file_id=file_obj.pk):
        row.delete()


def embed_query_text(text):
    """The vector of *text* in the configured backend's space, as floats.

    Runs the backend in this process: the worker's side of
    ``embed_query``.
    """
    return [float(x) for x in get_embedding_backend().embed_text(text)]


def embed_query(text):
    """The vector of a search's *text*, embedded by the worker.

    The web process never loads a model: the text goes to the worker at
    interactive priority, and the answer is cached per model. Raises
    QueryEmbeddingUnavailable when it does not come back in
    PHOTOS_EMBEDDING_QUERY_TIMEOUT seconds, or the backend failed.
    """
    from workspace.common.task_priority import INTERACTIVE_PRIORITY

    from ..tasks import embed_query_text as task

    text = " ".join(text.split())[:MAX_QUERY_LENGTH]
    key = _query_cache_key(text)
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        timeout = settings.PHOTOS_EMBEDDING_QUERY_TIMEOUT
        # Expires with the wait: a worker picking it up later would embed
        # for nobody.
        result = task.apply_async(
            (text,), priority=INTERACTIVE_PRIORITY, expires=timeout
        )
        vector = result.get(timeout=timeout)
    except Exception as exc:
        logger.warning("Could not embed a photo search: %s", scrub(str(exc)))
        raise QueryEmbeddingUnavailable(str(exc)) from exc
    cache.set(key, vector, _QUERY_CACHE_TTL)
    return vector


def _query_cache_key(text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    model = hashlib.sha256(
        get_embedding_backend().model_key.encode("utf-8")
    ).hexdigest()[:16]
    return _QUERY_CACHE_KEY.format(model=model, text=digest)

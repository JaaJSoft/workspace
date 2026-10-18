"""Embed the photos and videos of the library, in a space shared with text
(see services/embedding/): one vector per photo, one per key frame of a video
(video_key_frames.py).

Runs in the Celery worker: after the file's content lands (an upload
processor, see services/handlers.py, queued at low priority) or from the
hourly catch-up (background priority). A photo is decoded once, at the
backend's ``decode_size``; a video only where ffmpeg is installed. A file's
rows and their vectors are written in one transaction, and only if the file
still holds the bytes that were read.

Every photo and video of the library is embedded, a group folder's too:
unlike a face, a whole-picture vector says nothing about who anyone is.
Whoever reads the vectors re-enters the library through
``queries.library_files`` (services/similarity.py), so a trashed,
quarantined, hidden or unshared file never surfaces through them.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from PIL import Image

from workspace.common.logging import scrub
from workspace.common.metrics import safe_counter, safe_histogram
from workspace.common.uuids import uuid_v7_or_v4
from workspace.common.vectors.encoding import from_bytes
from workspace.common.vectors.indexing import index_vector
from workspace.files.models import File
from workspace.files.services.raster_formats import RASTER_LABELS
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked

from ..indexes import PHOTO_EMBEDDINGS
from ..models import MediaItem, PhotoEmbedding
from .analysis import library_candidates, media_type_for
from .auto_tags import tag_file_safely
from .embedding.registry import (
    embeddings_available,
    get_embedding_backend,
    is_misconfigured,
)
from .photo_decoding import decode_photo
from .video_frames import video_frames_available
from .video_key_frames import embed_key_frames

logger = logging.getLogger(__name__)

# A query text's vector, per model: paging through results or coming back to
# a search embeds nothing again.
_QUERY_CACHE_KEY = "photos:embedding:query:{model}:{text}"
_QUERY_CACHE_TTL = 24 * 3600
# Longer queries are cut: the text encoder reads 77 tokens anyway.
MAX_QUERY_LENGTH = 300
# A key frame's picture is a tile, not a print.
_FRAME_QUALITY = 80

_DURATION = safe_histogram(
    "workspace_photos_embedding_seconds",
    "Time spent embedding one photo or video.",
    labels=("backend", "media_type"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
_RESULTS = safe_counter(
    "workspace_photos_embedding_total",
    "Photos and videos embedded, by outcome.",
    labels=("result", "media_type"),
)


class QueryEmbeddingUnavailable(RuntimeError):
    """The query text could not be embedded: no worker answered in time, or
    the backend failed."""


def embedding_media_type(file_obj):
    """The kind of library item *file_obj* is when it can be embedded: a
    photo, or a video where ffmpeg is installed. None otherwise."""
    media_type = media_type_for(file_obj)
    if media_type == MediaItem.MediaType.VIDEO and not video_frames_available():
        return None
    return media_type


def embedded_media_types():
    """The kinds of library items this instance embeds: what the photo menu
    offers similar photos on."""
    if not embeddings_available():
        return []
    if not video_frames_available():
        return [MediaItem.MediaType.PHOTO.value]
    return [MediaItem.MediaType.PHOTO.value, MediaItem.MediaType.VIDEO.value]


def is_embedding_candidate(file_obj):
    """Whether *file_obj* is a photo or a video this instance embeds."""
    return (
        embeddings_available()
        and embedding_media_type(file_obj) is not None
        and file_obj.deleted_at is None
        and not is_blocked(file_obj)
    )


def embeddings_catch_up_enabled():
    """The catch-up only queues work that can run: a misconfigured backend
    shows on the admin dashboard instead of failing every hour."""
    return embeddings_available() and not is_misconfigured()


def pending_embeddings_qs(*, reanalyze=False):
    """The live photos and videos whose embeddings are missing or stale.

    Without ffmpeg a video is never pending: nothing could ever read it.
    Stale: other bytes, another owner (the index partition), or another
    model wrote them; a file's rows are written together, so any of them
    speaks for all. A file whose own hash is empty only counts when it has no
    row at all, as for MediaItem.
    """
    qs = exclude_blocked(library_candidates(File.objects.alive().with_blob()))
    if not video_frames_available():
        qs = qs.filter(type__in=RASTER_LABELS)
    if reanalyze:
        return qs
    current = PhotoEmbedding.objects.filter(
        file=OuterRef("pk"),
        owner_id=OuterRef("owner_id"),
        model=get_embedding_backend().model_key,
    )
    return qs.exclude(
        Q(Exists(current.filter(content_hash=OuterRef("content_hash"))))
        | Q(Q(content_hash="") & Exists(current))
    )


def refresh_embedding(file_obj):
    """Embed *file_obj* for the catch-up; True when its rows were written."""
    return embed_file(file_obj) is not None


def embed_file(file_obj):
    """Embed *file_obj* and store its rows; return them, or None.

    One row for a photo, one per key frame for a video. None means nothing
    was stored: the file is not a candidate, its blob could not be opened,
    the backend failed, or the file changed while it was read. A file that
    cannot be decoded, or is past the limits, is stored as one row without a
    vector, so it does not come back every hour.
    """
    if not is_embedding_candidate(file_obj):
        return None
    backend = get_embedding_backend()
    model_key = backend.model_key
    existing = list(PhotoEmbedding.objects.filter(file_id=file_obj.pk))
    if (
        existing
        and file_obj.content_hash
        and all(
            row.model == model_key and row.content_hash == file_obj.content_hash
            for row in existing
        )
    ):
        # Same bytes, same model: only the partition moved with the file.
        return _tag(_move(file_obj, existing))

    media_type = embedding_media_type(file_obj)
    embed = _embed_video if media_type == MediaItem.MediaType.VIDEO else _embed_photo
    started = time.monotonic()
    try:
        frames = embed(file_obj, backend)
    except Exception:
        _RESULTS.labels(result="error", media_type=media_type).inc()
        logger.exception("Embedding failed for %s", scrub(file_obj.content.name))
        return None
    if frames is None:
        _RESULTS.labels(result="skipped", media_type=media_type).inc()
        return None
    _DURATION.labels(backend=backend.key, media_type=media_type).observe(
        time.monotonic() - started
    )
    embedded = any(frame.vector is not None for frame in frames)
    _RESULTS.labels(
        result="embedded" if embedded else "undecodable", media_type=media_type
    ).inc()
    return _tag(_store(file_obj, model_key, file_obj.content_hash, frames))


def _tag(rows):
    """Tag the file of freshly written *rows*, then hand them back. Here
    rather than left to the catch-up: the file is tagged within moments of
    its upload, while the backend is loaded."""
    if rows:
        # One file: its first row reads all of them.
        tag_file_safely(rows[0])
    return rows


@dataclass(frozen=True)
class _Row:
    """What one row stores: a vector (None when unreadable), and for a key
    frame its time and its picture."""

    vector: object
    timestamp: float | None = None
    image: Image.Image | None = None


def _embed_photo(file_obj, backend):
    """The photo's row, its vector None when the photo cannot be decoded or
    is too large; None when its blob could not be opened (the catch-up tries
    again)."""
    if file_obj.size and file_obj.size > settings.PHOTOS_EMBEDDING_MAX_FILE_BYTES:
        return [_Row(vector=None)]
    image = decode_photo(file_obj, backend.decode_size, reader="Photo embedding")
    if image is False:
        return None
    return [_Row(vector=None if image is None else backend.embed_image(image))]


def _embed_video(file_obj, backend):
    """A row per key frame, or one without a vector for a video with none;
    None when its blob could not be opened."""
    key_frames = embed_key_frames(file_obj, backend)
    if key_frames is None:
        return None
    if not key_frames:
        return [_Row(vector=None)]
    return [
        _Row(vector=frame.vector, timestamp=frame.timestamp, image=frame.image)
        for frame in key_frames
    ]


def frame_path(file_id, row_id):
    return f"video-frames/{file_id}/{row_id}.webp"


def _save_frame(file_id, row_id, image):
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=_FRAME_QUALITY)
    return default_storage.save(
        frame_path(file_id, row_id), ContentFile(buffer.getvalue())
    )


def delete_frames(names):
    for name in names:
        try:
            default_storage.delete(name)
        except OSError:
            logger.warning("Could not delete video frame %s", scrub(name))


def _is_current(file_obj, read_hash):
    """Whether *file_obj* still holds the bytes read, for its owner, out of
    the trash; locks its row until the transaction ends."""
    return (
        File.objects.select_for_update()
        .filter(
            pk=file_obj.pk,
            content_hash=read_hash,
            owner_id=file_obj.owner_id,
            deleted_at__isnull=True,
        )
        .exists()
    )


def _store(file_obj, model_key, read_hash, frames):
    """Replace the rows of *file_obj* with one per _Row of *frames*; return
    them, or None when the file changed since it was read."""
    pks = [uuid_v7_or_v4() for _frame in frames]
    # Written ahead of the transaction, as face crops are, and taken back if
    # it stores nothing.
    pictures = []
    try:
        for pk, frame in zip(pks, frames, strict=True):
            pictures.append(
                _save_frame(file_obj.pk, pk, frame.image) if frame.image else ""
            )
        with transaction.atomic():
            if not _is_current(file_obj, read_hash):
                # Moved, replaced or trashed while it was read: whatever
                # changed it queues its own pass, or the catch-up does.
                delete_frames(filter(None, pictures))
                return None
            # Instance deletes: the post_delete receiver drops the vectors
            # and the frames.
            PhotoEmbedding.objects.filter(file_id=file_obj.pk).delete()
            analyzed_at = timezone.now()
            rows = []
            for pk, frame, picture in zip(pks, frames, pictures, strict=True):
                row = PhotoEmbedding.objects.create(
                    uuid=pk,
                    file_id=file_obj.pk,
                    owner_id=file_obj.owner_id,
                    content_hash=read_hash,
                    model=model_key,
                    timestamp=frame.timestamp,
                    frame=picture,
                    analyzed_at=analyzed_at,
                )
                if frame.vector is not None:
                    index_vector(PHOTO_EMBEDDINGS, row.pk, frame.vector)
                    # Written by the index, normalized: the row in hand has none.
                    row.refresh_from_db(fields=["embedding"])
                rows.append(row)
    except Exception:
        delete_frames(filter(None, pictures))
        raise
    return rows


def _move(file_obj, rows):
    """Move the rows of a file given to another owner to their partition;
    return them, or None when the file changed in the meantime."""
    with transaction.atomic():
        if not _is_current(file_obj, file_obj.content_hash):
            return None
        # A new owner is tagged again, with their own tags; the rows of a
        # file that kept its owner stay as tagged as they were.
        PhotoEmbedding.objects.filter(file_id=file_obj.pk).exclude(
            owner_id=file_obj.owner_id
        ).update(owner_id=file_obj.owner_id, auto_tagged_at=None)
        for row in rows:
            if row.owner_id != file_obj.owner_id:
                row.auto_tagged_at = None
            row.owner_id = file_obj.owner_id
            if row.embedding:
                # Re-derives the partition from the row.
                index_vector(
                    PHOTO_EMBEDDINGS,
                    row.pk,
                    from_bytes(row.embedding, PHOTO_EMBEDDINGS.dims),
                )
    return rows


def forget_embedding(file_obj):
    """Drop the embeddings of a file that stopped being a photo or a video."""
    # Instance deletes: the post_delete receiver drops the vectors and frames.
    PhotoEmbedding.objects.filter(file_id=file_obj.pk).delete()


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

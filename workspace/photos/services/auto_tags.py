"""Automatic tags: each photo and video of the library tagged with what it
shows.

The tags are the classic ones (tag_vocabulary.CLASSIC_TAGS) the configured
backend recognizes: its ``tag_thresholds`` table, fitted per tag by the
embedding bench, names those it tells apart well enough and how close a
photo's vector must lie to the tag name's. A video is tagged from its key
frames, at its own table (``video_tag_thresholds``): a tag lands when the
closest of its key frames is under the tag's threshold. A tag a table leaves
out is never placed by that backend. Each lands as a FileTag of source AUTO
on the owner's own Tag for that classic tag (ClassicTagMapping): the one
placed for it before, whatever it is called now, else the tag of its name in
the owner's language (matched case-insensitively, created on first use with
the classic tag's icon).

A file is tagged once, after it is embedded (``PhotoEmbedding.auto_tagged_at``,
stamped on every row of the file, reset with every new vector): a tag its
owner took off stays off.

Only personal files: a Tag belongs to its owner, and the files API tags
nothing else. Runs in the worker, after the embedding and from the hourly
catch-up; the web process never runs a model.
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np
from django.core.cache import cache
from django.db.models import Exists, OuterRef
from django.utils import timezone, translation
from django.utils.translation import gettext

from workspace.common.logging import scrub
from workspace.common.vectors.encoding import from_bytes, normalize, to_bytes
from workspace.files.models import File, FileTag
from workspace.files.services.tags import get_or_create_tag
from workspace.users.services.settings import get_user_language

from ..indexes import PHOTO_EMBEDDINGS
from ..models import ClassicTagMapping, PhotoEmbedding
from .embedding.registry import (
    embeddings_available,
    get_embedding_backend,
    is_misconfigured,
    tag_thresholds,
    video_tag_thresholds,
)
from .preferences import auto_tags
from .tag_vocabulary import CLASSIC_TAGS

logger = logging.getLogger(__name__)

# The vocabulary's vectors, per model and vocabulary: embedded once.
_VOCABULARY_CACHE_KEY = "photos:auto_tags:vocabulary:{}"


def auto_tags_enabled():
    return embeddings_available() and not is_misconfigured()


def vocabulary(thresholds):
    """The classic tags of the table *thresholds* ({tag text: max distance}),
    their name vectors (unit length, one row each) and their thresholds.
    Embedded by the backend in this process on a cold cache, which loads its
    text encoder."""
    backend = get_embedding_backend()
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


def tags_for(vectors, thresholds):
    """The classic tags of the table *thresholds* the frames *vectors* (unit
    length, a row each: a photo's one, a video's key frames) show: those
    whose closest frame is under their threshold."""
    tags, tag_vectors, limits = vocabulary(thresholds)
    if not tags:
        return []
    closest = (1.0 - tag_vectors @ np.atleast_2d(vectors).T).min(axis=1)
    return [tag for tag, hit in zip(tags, closest <= limits, strict=True) if hit]


def owner_tag_ids(owner, classics):
    """The uuids of *owner*'s tags for the classic tags *classics*: the one
    each was placed as before, else the tag of its name in the owner's
    language, created and remembered on first use. A rename or a language
    change never makes a second tag of one classic tag."""
    tag_ids = dict(
        ClassicTagMapping.objects.filter(
            owner=owner, label__in=[classic.label for classic in classics]
        ).values_list("label", "tag_id")
    )
    missing = [classic for classic in classics if classic.label not in tag_ids]
    if missing:
        with translation.override(get_user_language(owner)):
            for classic in missing:
                tag = get_or_create_tag(owner, gettext(classic.name), icon=classic.icon)
                mapping, _ = ClassicTagMapping.objects.get_or_create(
                    owner=owner, label=classic.label, defaults={"tag": tag}
                )
                tag_ids[classic.label] = mapping.tag_id
    return {tag_ids[classic.label] for classic in classics}


def tag_file(row):
    """Give the photo or video of the PhotoEmbedding *row* its automatic
    tags, once: a video from all its key frames together.

    Returns True when the file is settled: tagged, or nothing to tag (no
    vector, a group file, trashed, the owner turned the tags off). A file
    tagged already is left as it is.
    """
    if row.auto_tagged_at is not None:
        return True
    file_obj = row.file
    # The file's rows are written together: those of the vector in hand.
    rows = list(
        PhotoEmbedding.objects.filter(
            file_id=row.file_id,
            owner_id=row.owner_id,
            model=row.model,
            content_hash=row.content_hash,
        ).values_list("pk", "timestamp", "embedding")
    )
    vectors = [
        from_bytes(embedding, PHOTO_EMBEDDINGS.dims)
        for _pk, _timestamp, embedding in rows
        if embedding is not None
    ]
    taggable = (
        vectors
        and file_obj.group_id is None
        and file_obj.deleted_at is None
        and file_obj.owner_id == row.owner_id
        and auto_tags(file_obj.owner)
    )
    if taggable:
        is_video = any(timestamp is not None for _pk, timestamp, _e in rows)
        thresholds = video_tag_thresholds() if is_video else tag_thresholds()
        tag_ids = owner_tag_ids(file_obj.owner, tags_for(np.stack(vectors), thresholds))
        # A tag the file already carries, by hand or not, keeps its row.
        FileTag.objects.bulk_create(
            [
                FileTag(tag_id=tag_id, file=file_obj, source=FileTag.Source.AUTO)
                for tag_id in tag_ids
            ],
            ignore_conflicts=True,
        )
    # By primary key and owner: new vectors written meanwhile are new rows,
    # and a file given to someone else had its stamps reset; either is tagged
    # again.
    PhotoEmbedding.objects.filter(
        pk__in=[pk for pk, _timestamp, _embedding in rows], owner_id=row.owner_id
    ).update(auto_tagged_at=timezone.now())
    return True


def tag_file_safely(row):
    """tag_file for a caller whose own work must not fail with it (the
    embedding pipeline): a failure is logged, and the catch-up retries."""
    try:
        return tag_file(row)
    except Exception:
        logger.exception("Automatic tagging failed for %s", scrub(row.file_id))
        return False


def pending_auto_tags_qs(*, reanalyze=False):
    """The live personal photos and videos embedded with a vector of the
    current model but not tagged yet."""
    rows = PhotoEmbedding.objects.filter(
        file=OuterRef("pk"),
        embedding__isnull=False,
        model=get_embedding_backend().model_key,
    )
    if not reanalyze:
        rows = rows.filter(auto_tagged_at__isnull=True)
    return File.objects.alive().filter(Exists(rows), group__isnull=True)


def refresh_auto_tags(file_obj):
    """The catch-up's processor: tag one pending photo or video."""
    row = (
        PhotoEmbedding.objects.filter(
            file_id=file_obj.pk,
            embedding__isnull=False,
            model=get_embedding_backend().model_key,
        )
        .select_related("file__owner")
        .first()
    )
    return row is not None and tag_file(row)

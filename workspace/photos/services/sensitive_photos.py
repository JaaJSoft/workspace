"""Sensitive photos: blurred wherever they show as a thumbnail.

A photo is sensitive when the detector says so (services/sensitivity/), run
in the Celery worker after its MediaItem row is written (an upload processor,
see services/handlers.py) or by the hourly catch-up; or when someone who can
edit it marks it by hand, a choice the detector never overrides. The flag
sits on the MediaItem row, not on a tag of the owner's: everyone who can open
the photo sees it blurred, in a share or a shared album too.

Videos are not checked yet; they can be marked by hand.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import BooleanField, F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from workspace.common.logging import scrub
from workspace.common.metrics import safe_counter
from workspace.files.models import File
from workspace.files.services.raster_formats import RASTER_LABELS
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked

from ..models import MediaItem
from .analysis import library_candidates, media_type_for
from .photo_decoding import decode_photo
from .sensitivity.registry import (
    detection_available,
    get_sensitive_backend,
    is_misconfigured,
    sensitive_threshold,
)

logger = logging.getLogger(__name__)

_RESULTS = safe_counter(
    "workspace_photos_sensitive_total",
    "Photos checked for sensitive content, by outcome.",
    labels=("result",),
)


def is_sensitive_candidate(file_obj):
    """Whether *file_obj* is a photo this instance checks."""
    return (
        detection_available()
        and media_type_for(file_obj) == MediaItem.MediaType.PHOTO
        and file_obj.deleted_at is None
        and not is_blocked(file_obj)
    )


def sensitive_catch_up_enabled():
    """The catch-up only queues work that can run: a misconfigured backend
    shows on the admin dashboard instead of failing every hour."""
    return detection_available() and not is_misconfigured()


def pending_sensitive_qs(*, reanalyze=False):
    """The live library photos never checked, or checked on other bytes or by
    another detector.

    A photo without a MediaItem row is not in the library yet: its row's
    writer runs first, and the photo is pending from then on. A file whose
    own hash is empty only counts when it was never checked.
    """
    qs = exclude_blocked(
        library_candidates(File.objects.alive().with_blob()).filter(
            type__in=RASTER_LABELS, media_item__isnull=False
        )
    )
    if reanalyze:
        return qs
    return qs.filter(
        ~Q(media_item__sensitive_model=get_sensitive_backend().key)
        | (~Q(content_hash="") & ~Q(media_item__sensitive_hash=F("content_hash")))
    )


def refresh_sensitive(file_obj):
    """Check *file_obj* for the catch-up; True when its verdict was written."""
    return check_photo(file_obj)


def check_photo(file_obj):
    """Score *file_obj* and record the verdict on its MediaItem row; True
    when it was written.

    False means nothing was stored: the file is not a candidate, its blob
    could not be opened, the backend failed, or the file changed while it
    was read. A photo that cannot be decoded, or is past the size limit, is
    recorded without a score, so it does not come back every hour.
    """
    if not is_sensitive_candidate(file_obj):
        return False
    backend = get_sensitive_backend()
    read_hash = file_obj.content_hash
    try:
        score = _score(file_obj, backend)
    except Exception:
        _RESULTS.labels(result="error").inc()
        logger.exception(
            "Sensitive-content check failed for %s", scrub(file_obj.content.name)
        )
        return False
    if score is False:
        _RESULTS.labels(result="skipped").inc()
        return False
    verdict = score is not None and score >= sensitive_threshold(backend)
    written = MediaItem.objects.filter(
        file_id=file_obj.pk,
        file__content_hash=read_hash,
        file__deleted_at__isnull=True,
    ).update(
        sensitive_score=score,
        sensitive_model=backend.key,
        sensitive_hash=read_hash,
        sensitive=Coalesce(
            F("sensitive_by_user"), Value(verdict, output_field=BooleanField())
        ),
    )
    if written:
        if score is None:
            _RESULTS.labels(result="undecodable").inc()
        else:
            _RESULTS.labels(result="sensitive" if verdict else "clear").inc()
    return bool(written)


def _score(file_obj, backend):
    """The photo's score; None when it cannot be decoded or is too large,
    False when its blob could not be opened (the catch-up tries again)."""
    if file_obj.size and file_obj.size > settings.PHOTOS_SENSITIVE_MAX_FILE_BYTES:
        return None
    image = decode_photo(file_obj, backend.decode_size, reader="Sensitive check")
    if image is None or image is False:
        return image
    return backend.score(image)


def mark_sensitive(file_uuids, sensitive):
    """Mark the photos and videos *file_uuids* sensitive or not, by hand;
    returns how many were marked. Files outside the library are skipped."""
    return MediaItem.objects.filter(file_id__in=file_uuids).update(
        sensitive_by_user=sensitive, sensitive=sensitive
    )


def sensitive_flag():
    """``MediaItem.sensitive`` of the outer File row, null when it has none:
    the expression files reads through its sensitivity hook."""
    return Subquery(
        MediaItem.objects.filter(file_id=OuterRef("pk")).values("sensitive")[:1],
        output_field=BooleanField(null=True),
    )

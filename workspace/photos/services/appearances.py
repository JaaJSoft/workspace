"""Find the people of a photo by their body, and store them.

Faces miss whoever is seen from behind, turned away, too far or too blurred.
A person detector finds them all, and each one's torso is embedded with the
photo embedding backend: what someone wears, which names them within a few
hours of a photo where their face did (appearance_suggestions.py).

Part of face grouping: the same opt-in, the same owner's personal photos,
the same purge. Runs in the Celery worker, queued once a photo's faces are
stored (face_analysis.py) or from the hourly catch-up. Photos only: a video's
people are its face tracking's.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from PIL import Image

from workspace.common.logging import scrub
from workspace.common.vectors.encoding import normalize, to_bytes
from workspace.files.models import File
from workspace.files.services.raster_formats import RASTER_LABELS
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked

from ..models import Appearance, AppearanceAnalysis, MediaItem
from .analysis import library_candidates, media_type_for
from .detection.nanodet import detect_people
from .embedding.registry import (
    embeddings_available,
    get_embedding_backend,
)
from .embedding.registry import (
    is_misconfigured as embedding_misconfigured,
)
from .face_analysis import _iou, crop_path
from .face_preferences import (
    faces_available,
    faces_enabled,
    lock_opt_in,
    opted_in_owner_ids,
)
from .photo_decoding import decode_photo

logger = logging.getLogger(__name__)

# Raise when the pipeline starts writing something new: every photo read by
# an older version counts as pending again.
APPEARANCE_ANALYSIS_VERSION = 1

# The torso, under the head: as wide as the person's box, from TORSO_TOP of
# that width below its top (the head) down to TORSO_BOTTOM (the waist), cut
# short by the box. The thresholds of the embedding backends are calibrated
# on torsos cut this way.
TORSO_TOP = 0.4
TORSO_BOTTOM = 1.6
# A torso shorter than this share of its width is a person cut off at the
# shoulders: it shows no clothes.
MIN_TORSO_HEIGHT = 0.5
# Torsos narrower than this, in px of the photo decoded at
# PHOTOS_FACES_DECODE_SIZE, show too little to tell clothes apart.
MIN_TORSO_WIDTH = 128
# The most people kept per photo, the most confident first.
MAX_PER_PHOTO = 20
# A new person at the place of an old one keeps what the user said of them.
_SAME_PERSON_IOU = 0.5
# Height of the WebP shown for a torso, in px.
CROP_HEIGHT = 200
_CROP_QUALITY = 80


def appearances_available():
    """Whether this instance finds people by their body.

    Needs face grouping, and an embedding backend calibrated for torsos: a
    remote one never is, so no picture of a person leaves the server.
    """
    return (
        bool(settings.PHOTOS_APPEARANCES_ENABLED)
        and faces_available()
        and embeddings_available()
        and not embedding_misconfigured()
        and get_embedding_backend().appearance_max_distance is not None
    )


def is_appearance_candidate(file_obj):
    """Whether *file_obj* is a photo whose people its owner wants found."""
    return (
        appearances_available()
        and media_type_for(file_obj) == MediaItem.MediaType.PHOTO
        and file_obj.group_id is None
        and file_obj.deleted_at is None
        and not is_blocked(file_obj)
        and faces_enabled(file_obj.owner)
    )


def appearances_catch_up_enabled():
    return appearances_available()


def pending_appearances_qs(*, reanalyze=False):
    """The personal photos of opted-in users whose people are missing or
    stale: other bytes, another owner, another model, or an older version of
    this pipeline read them."""
    qs = exclude_blocked(
        library_candidates(File.objects.alive().with_blob()).filter(
            type__in=RASTER_LABELS,
            group__isnull=True,
            owner_id__in=opted_in_owner_ids(),
        )
    )
    if reanalyze:
        return qs
    return qs.filter(
        Q(appearance_analysis__isnull=True)
        | ~Q(appearance_analysis__owner_id=F("owner_id"))
        | ~Q(appearance_analysis__model=get_embedding_backend().model_key)
        | Q(appearance_analysis__version__lt=APPEARANCE_ANALYSIS_VERSION)
        | (
            ~Q(content_hash="")
            & ~Q(appearance_analysis__content_hash=F("content_hash"))
        )
    )


def refresh_appearances(file_obj):
    """Analyze *file_obj* for the catch-up; True when its analysis was stored."""
    return analyze_appearances(file_obj) is not None


def queue_appearances(file_obj):
    """Queue the analysis of *file_obj*'s people, once the caller commits."""
    if is_appearance_candidate(file_obj):
        from ..tasks import analyze_photo_appearances

        uuid = str(file_obj.pk)
        transaction.on_commit(lambda: analyze_photo_appearances.delay(uuid))


@dataclass
class _Found:
    row: Appearance
    vector: np.ndarray
    crop: bytes


def analyze_appearances(file_obj):
    """Find and store the people of *file_obj*; return the new rows.

    None means nothing was stored: the file is not a candidate, its blob
    could not be opened, a model failed, or the file changed while it was
    read. A photo that cannot be decoded is stored as analyzed with nobody,
    so it does not come back every hour; so is one past the size limit.
    """
    if not is_appearance_candidate(file_obj):
        return None
    backend = get_embedding_backend()
    try:
        found = _find(file_obj, backend)
    except Exception:
        logger.exception(
            "Appearance analysis failed for %s", scrub(file_obj.content.name)
        )
        return None
    if found is None:
        return None
    rows = _store(file_obj, backend.model_key, found)
    if rows is not None:
        from .appearance_suggestions import suggest_around

        suggest_around(file_obj)
    return rows


def torso_box(box, width, height):
    """The torso of a person boxed by *box* (x, y, w, h), as (left, top,
    right, bottom) inside a *width* x *height* picture, or None when it is
    too small or cut off to show any clothes."""
    x, y, w, h = box
    left, right = max(0.0, x), min(float(width), x + w)
    top = y + TORSO_TOP * w
    bottom = min(float(height), y + h, y + TORSO_BOTTOM * w)
    if right - left < MIN_TORSO_WIDTH or bottom - top < MIN_TORSO_HEIGHT * w:
        return None
    return round(left), round(top), round(right), round(bottom)


def _find(file_obj, backend):
    if file_obj.size and file_obj.size > settings.PHOTOS_FACES_MAX_FILE_BYTES:
        return []
    image = decode_photo(
        file_obj, settings.PHOTOS_FACES_DECODE_SIZE, reader="Appearance analysis"
    )
    if image is False:
        return None
    if image is None:
        return []
    width, height = image.size
    people = sorted(
        detect_people(np.asarray(image)), key=lambda p: p.score, reverse=True
    )
    found = []
    for person in people:
        torso = torso_box(person.box, width, height)
        if torso is None:
            continue
        crop = image.crop(torso)
        found.append(
            _Found(
                row=_row(file_obj, person, width, height),
                vector=normalize(backend.embed_image(crop), backend.dims),
                crop=_webp(crop),
            )
        )
        if len(found) == MAX_PER_PHOTO:
            break
    return found


def _row(file_obj, person, width, height):
    x, y, w, h = person.box
    return Appearance(
        file_id=file_obj.pk,
        owner_id=file_obj.owner_id,
        box_x=_fraction(x, width),
        box_y=_fraction(y, height),
        box_width=_fraction(w, width),
        box_height=_fraction(h, height),
        detector_score=round(person.score, 4),
    )


def _fraction(value, total):
    return min(1.0, max(0.0, float(value) / total))


def _webp(crop):
    scale = CROP_HEIGHT / crop.height
    if scale < 1:
        crop = crop.resize(
            (max(1, round(crop.width * scale)), CROP_HEIGHT), Image.LANCZOS
        )
    buffer = io.BytesIO()
    crop.save(buffer, format="WEBP", quality=_CROP_QUALITY)
    return buffer.getvalue()


def _store(file_obj, model_key, found):
    """Swap the photo's people for *found*; return the new rows, or None
    when the file changed or face grouping was turned off meanwhile."""
    read_hash = file_obj.content_hash
    owner_id = file_obj.owner_id
    crops = []
    try:
        for item in found:
            item.row.embedding = to_bytes(item.vector)
            crops.append(
                default_storage.save(
                    crop_path(owner_id, item.row.pk), ContentFile(item.crop)
                )
            )
            item.row.crop = crops[-1]
        with transaction.atomic():
            current = (
                File.objects.select_for_update()
                .filter(
                    pk=file_obj.pk,
                    content_hash=read_hash,
                    owner_id=owner_id,
                    group__isnull=True,
                    deleted_at__isnull=True,
                )
                .exists()
            )
            if not current or not lock_opt_in(owner_id):
                _delete_crops(crops)
                return None
            rows = _replace(file_obj, [item.row for item in found])
            AppearanceAnalysis.objects.update_or_create(
                file_id=file_obj.pk,
                defaults={
                    "owner_id": owner_id,
                    "content_hash": read_hash,
                    "model": model_key,
                    "version": APPEARANCE_ANALYSIS_VERSION,
                    "appearance_count": len(rows),
                    "analyzed_at": timezone.now(),
                },
            )
    except Exception:
        _delete_crops(crops)
        raise
    return rows


def _replace(file_obj, rows):
    """Swap the file's people for *rows*, keeping what the user said: a new
    person at the place of an old one (same box, give or take) inherits who
    they are, who they are not, and whether they were hidden."""
    old_rows = list(Appearance.objects.filter(file_id=file_obj.pk))
    pairs = sorted(
        (
            (_iou(old, new), i, j)
            for i, old in enumerate(old_rows)
            for j, new in enumerate(rows)
        ),
        reverse=True,
    )
    used_old, used_new = set(), set()
    for score, i, j in pairs:
        if score < _SAME_PERSON_IOU:
            break
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        old, new = old_rows[i], rows[j]
        if old.owner_id != new.owner_id:
            continue
        new.person_id = old.person_id
        new.rejected_person_id = old.rejected_person_id
        new.hidden = old.hidden
    # Instance deletes: the post_delete receiver drops each crop.
    for old in old_rows:
        old.delete()
    for row in rows:
        row.save()
    return rows


def _delete_crops(names):
    for name in names:
        try:
            default_storage.delete(name)
        except OSError:
            logger.warning("Could not delete appearance crop %s", scrub(name))


def forget_appearances(file_obj):
    """Drop the people and the analysis of a photo that left its owner's
    library, or stopped being a photo."""
    with transaction.atomic():
        for row in Appearance.objects.filter(file_id=file_obj.pk):
            row.delete()
        AppearanceAnalysis.objects.filter(file_id=file_obj.pk).delete()

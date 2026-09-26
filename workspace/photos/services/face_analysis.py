"""Find the faces of a photo: detect, align, score, embed, crop, store.

Runs in the Celery worker, after the photo's MediaItem analysis (see
services/handlers.py) or from the hourly catch-up. The original is decoded
once, at PHOTOS_FACES_DECODE_SIZE, and every face comes out of that copy.
The rows and their vectors are written in one transaction; grouping them
(services/face_grouping.py) follows once it has committed.

Only the owner's personal photos are read: a group folder belongs to several
people, and whose library a face there would join has no good answer.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from workspace.common.logging import scrub
from workspace.common.metrics import safe_counter, safe_histogram
from workspace.common.vectors.indexing import index_vector
from workspace.files.models import File
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked
from workspace.files.services.thumbnails.generation import RASTER_LABELS

from ..indexes import FACE_EMBEDDINGS
from ..models import Face, FaceAnalysis, FaceCluster
from .detection.base import FaceBackend
from .detection.geometry import ALIGNED_SIZE, align
from .detection.registry import get_face_backend, is_misconfigured
from .face_preferences import faces_available, faces_enabled, opted_in_owner_ids

logger = logging.getLogger(__name__)

# Raise when the pipeline starts writing something new: every photo analyzed
# by an older version counts as pending again.
FACE_ANALYSIS_VERSION = 1

# Side of the square WebP shown for a face, in px.
CROP_SIZE = 160
_CROP_QUALITY = 80
# The crop takes in this much of the face's surroundings: hair, chin, ears.
_CROP_MARGIN = 1.6

# A face at least this tall (px, at the decode size) scores full marks for
# size: the embedding models see 112 px.
_FULL_SIZE = ALIGNED_SIZE
# A face kept from an old analysis passes its grouping on to the new face at
# the same place: a reanalysis must not undo what the user corrected.
_SAME_FACE_IOU = 0.5

_DURATION = safe_histogram(
    "workspace_photos_face_analysis_seconds",
    "Time spent analyzing one photo for faces.",
    labels=("backend",),
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
_RESULTS = safe_counter(
    "workspace_photos_face_analysis_total",
    "Photos analyzed for faces, by outcome.",
    labels=("result",),
)
_DETECTED = safe_counter(
    "workspace_photos_faces_detected_total",
    "Faces stored by the face analysis.",
)


def is_face_candidate(file_obj):
    """Whether *file_obj* is a photo whose faces its owner wants found."""
    return (
        faces_available()
        and file_obj.node_type == File.NodeType.FILE
        and file_obj.type in RASTER_LABELS
        and bool(file_obj.content)
        and file_obj.group_id is None
        and file_obj.deleted_at is None
        and not is_blocked(file_obj)
        and faces_enabled(file_obj.owner)
    )


def faces_catch_up_enabled():
    """The catch-up only queues work that can run: a misconfigured backend
    shows on the admin dashboard instead of failing every hour."""
    return faces_available() and not is_misconfigured()


def pending_faces_qs(*, reanalyze=False):
    """The personal photos of opted-in users whose faces are missing or stale.

    Stale: other bytes, another backend, another owner, or an older version
    of this pipeline wrote them.
    """
    qs = exclude_blocked(
        File.objects.alive()
        .with_blob()
        .filter(
            node_type=File.NodeType.FILE,
            type__in=RASTER_LABELS,
            group__isnull=True,
            owner_id__in=opted_in_owner_ids(),
        )
    )
    if reanalyze:
        return qs
    return qs.filter(
        Q(face_analysis__isnull=True)
        | ~Q(face_analysis__owner_id=F("owner_id"))
        | ~Q(face_analysis__backend=get_face_backend().key)
        | Q(face_analysis__version__lt=FACE_ANALYSIS_VERSION)
        | (~Q(content_hash="") & ~Q(face_analysis__content_hash=F("content_hash")))
    )


def refresh_faces(file_obj):
    """Analyze *file_obj* for the catch-up; True when its analysis was stored."""
    return analyze_faces(file_obj) is not None


@dataclass
class _Found:
    face: Face
    embedding: np.ndarray
    crop: bytes


def analyze_faces(file_obj):
    """Detect and store the faces of *file_obj*; return the new Face rows.

    None means nothing was stored: the photo is not a candidate, its blob
    could not be opened, the backend failed, or the file changed while it was
    read. A photo that cannot be decoded is stored as analyzed with no face,
    so it does not come back every hour.
    """
    if not is_face_candidate(file_obj):
        return None
    backend = get_face_backend()
    started = time.monotonic()
    try:
        faces = _analyze(file_obj, backend)
    except Exception:
        _RESULTS.labels(result="error").inc()
        logger.exception("Face analysis failed for %s", scrub(file_obj.content.name))
        return None
    _DURATION.labels(backend=backend.key).observe(time.monotonic() - started)
    if faces is None:
        _RESULTS.labels(result="skipped").inc()
        return None
    _RESULTS.labels(result="faces" if faces else "no_faces").inc()
    _DETECTED.inc(len(faces))

    from .face_grouping import assign_faces, maybe_queue_clustering

    assign_faces(file_obj.owner_id, [face.pk for face in faces])
    maybe_queue_clustering(file_obj.owner_id)
    return faces


def _analyze(file_obj, backend):
    read_hash = file_obj.content_hash
    owner_id = file_obj.owner_id
    if file_obj.size and file_obj.size > settings.PHOTOS_FACES_MAX_FILE_BYTES:
        found = []
    else:
        image = _decode(file_obj)
        if image is False:
            return None
        found = [] if image is None else _find(file_obj, image, backend)

    crops = []
    try:
        for item in found:
            crops.append(
                default_storage.save(
                    crop_path(owner_id, item.face.pk), ContentFile(item.crop)
                )
            )
            item.face.crop = crops[-1]
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
            if not current:
                # Moved, replaced or trashed while it was read: whatever
                # changed it queues its own analysis.
                _delete_crops(crops)
                return None
            faces = _replace_faces(file_obj, owner_id, found)
            FaceAnalysis.objects.update_or_create(
                file_id=file_obj.pk,
                defaults={
                    "owner_id": owner_id,
                    "content_hash": read_hash,
                    "backend": backend.key,
                    "version": FACE_ANALYSIS_VERSION,
                    "face_count": len(faces),
                    "analyzed_at": timezone.now(),
                },
            )
    except Exception:
        _delete_crops(crops)
        raise
    return faces


def _decode(file_obj):
    """The photo as displayed, at most PHOTOS_FACES_DECODE_SIZE on a side.

    RGB uint8. None when the bytes are not a picture Pillow can read, False
    when the blob could not be opened at all (the catch-up tries again).
    """
    size = settings.PHOTOS_FACES_DECODE_SIZE
    try:
        handle = file_obj.content.open("rb")
    except OSError as exc:
        logger.warning(
            "Face analysis cannot read the blob of %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return False
    try:
        with Image.open(handle) as img:
            # A JPEG decodes straight at a fraction of its size.
            img.draft("RGB", (size, size))
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            return np.asarray(img)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        logger.info(
            "Face analysis could not decode %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None
    finally:
        handle.close()


def _find(file_obj, image, backend: FaceBackend):
    backend.prepare()
    height, width = image.shape[:2]
    detected = [
        face
        for face in backend.detect(image)
        if min(face.box[2], face.box[3]) >= settings.PHOTOS_FACES_MIN_SIZE
    ]
    detected.sort(key=lambda face: face.score, reverse=True)
    found = []
    for detection in detected[: settings.PHOTOS_FACES_MAX_PER_PHOTO]:
        aligned = align(image, detection.landmarks)
        x, y, w, h = detection.box
        size_score = min(1.0, min(w, h) / _FULL_SIZE)
        quality = detection.score * (
            0.5 * size_score + 0.5 * backend.sharpness(aligned)
        )
        face = Face(
            file_id=file_obj.pk,
            owner_id=file_obj.owner_id,
            box_x=_fraction(x, width),
            box_y=_fraction(y, height),
            box_width=_fraction(w, width),
            box_height=_fraction(h, height),
            landmarks=[
                [round(_fraction(px, width), 4), round(_fraction(py, height), 4)]
                for px, py in detection.landmarks
            ],
            detector_score=round(detection.score, 4),
            quality=round(quality, 4),
        )
        found.append(
            _Found(
                face=face,
                embedding=backend.embed(aligned),
                crop=_crop(image, detection.box),
            )
        )
    return found


def _fraction(value, total):
    return min(1.0, max(0.0, float(value) / total))


def _crop(image, box):
    """The square WebP of a face and some of its surroundings."""
    height, width = image.shape[:2]
    x, y, w, h = box
    side = min(max(w, h) * _CROP_MARGIN, width, height)
    left = min(max(0.0, x + w / 2 - side / 2), width - side)
    top = min(max(0.0, y + h / 2 - side / 2), height - side)
    square = Image.fromarray(image).crop(
        (round(left), round(top), round(left + side), round(top + side))
    )
    square = square.resize((CROP_SIZE, CROP_SIZE), Image.LANCZOS)
    buffer = io.BytesIO()
    square.save(buffer, format="WEBP", quality=_CROP_QUALITY)
    return buffer.getvalue()


def crop_path(owner_id, face_uuid):
    return f"faces/{owner_id}/{face_uuid}.webp"


def _replace_faces(file_obj, owner_id, found):
    """Swap the photo's faces for *found*, keeping what the user decided.

    A new face at the place of an old one (same box, give or take) inherits
    its cluster, its assignment and its rejection, and becomes the cover in
    its place. Called inside the analysis transaction.
    """
    old_faces = list(Face.objects.filter(file_id=file_obj.pk))
    cover_of = dict(
        FaceCluster.objects.filter(cover__in=old_faces).values_list("cover_id", "pk")
    )
    inherited = _match_old_faces(old_faces, [item.face for item in found])
    new_covers = {}
    for new_face, old_face in inherited.items():
        if old_face.owner_id != owner_id:
            continue
        new_face.cluster_id = old_face.cluster_id
        new_face.assignment = old_face.assignment
        new_face.rejected_cluster_id = old_face.rejected_cluster_id
        if cover_of.get(old_face.pk) == old_face.cluster_id:
            new_covers[old_face.cluster_id] = new_face
    # Deleted first: the new rows may take the old ones' clusters, and the
    # database holds one face per photo per cluster.
    for old_face in old_faces:
        old_face.delete()
    faces = []
    for item in found:
        item.face.save()
        index_vector(FACE_EMBEDDINGS, item.face.pk, item.embedding)
        faces.append(item.face)
    for cluster_id, new_face in new_covers.items():
        FaceCluster.objects.filter(pk=cluster_id, cover__isnull=True).update(
            cover=new_face
        )
    return faces


def _match_old_faces(old_faces, new_faces):
    """{new face: old face} for the pairs whose boxes overlap enough."""
    pairs = sorted(
        (
            (_iou(old, new), i, j)
            for i, old in enumerate(old_faces)
            for j, new in enumerate(new_faces)
        ),
        reverse=True,
    )
    matched, used_old, used_new = {}, set(), set()
    for iou, i, j in pairs:
        if iou < _SAME_FACE_IOU:
            break
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        matched[new_faces[j]] = old_faces[i]
    return matched


def _iou(a, b):
    ax2, ay2 = a.box_x + a.box_width, a.box_y + a.box_height
    bx2, by2 = b.box_x + b.box_width, b.box_y + b.box_height
    w = max(0.0, min(ax2, bx2) - max(a.box_x, b.box_x))
    h = max(0.0, min(ay2, by2) - max(a.box_y, b.box_y))
    inter = w * h
    union = a.box_width * a.box_height + b.box_width * b.box_height - inter
    return inter / union if union > 0 else 0.0


def _delete_crops(names):
    for name in names:
        try:
            default_storage.delete(name)
        except OSError:
            logger.warning("Could not delete face crop %s", scrub(name))


def forget_faces(file_obj):
    """Drop the faces and the analysis of a photo that left its owner's library."""
    from .face_grouping import refresh_clusters

    with transaction.atomic():
        faces = list(Face.objects.filter(file_id=file_obj.pk))
        clusters = {face.cluster_id for face in faces if face.cluster_id}
        for face in faces:
            face.delete()
        FaceAnalysis.objects.filter(file_id=file_obj.pk).delete()
    refresh_clusters(clusters)

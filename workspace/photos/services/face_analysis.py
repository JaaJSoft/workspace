"""Find the faces of a photo or a video, and store them.

Runs in the Celery worker, after the file's MediaItem analysis (see
services/handlers.py) or from the hourly catch-up. A photo is decoded once, at
PHOTOS_FACES_DECODE_SIZE, and every face comes out of that copy
(face_images.py). A video yields one face per person seen in it
(face_video.py), and only where ffmpeg is installed. The rows and their
vectors are written in one transaction; grouping them
(services/face_grouping.py) follows once it has committed.

Only the owner's personal files are read: a group folder belongs to several
people, and whose library a face there would join has no good answer.
"""

from __future__ import annotations

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
from workspace.common.vectors.encoding import from_bytes
from workspace.common.vectors.indexing import index_vector
from workspace.files.models import File
from workspace.files.services.raster_formats import RASTER_LABELS
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked

from ..indexes import FACE_EMBEDDINGS
from ..models import Face, FaceAnalysis, FaceCluster, MediaItem
from .analysis import library_candidates, media_type_for
from .detection.registry import get_face_backend, is_misconfigured
from .face_images import detect_faces
from .face_preferences import (
    faces_available,
    faces_enabled,
    lock_opt_in,
    opted_in_owner_ids,
)
from .face_video import find_video_faces, video_faces_available

logger = logging.getLogger(__name__)

# Raise when the pipeline starts writing something new: every file analyzed
# by an older version counts as pending again.
FACE_ANALYSIS_VERSION = 2

# A face kept from an old analysis passes its grouping on to the new face at
# the same place: a reanalysis must not undo what the user corrected.
_SAME_FACE_IOU = 0.5
# In a video, the same person is the face whose embedding is this close, as a
# share of the grouping threshold: the frame it comes from may have changed.
_SAME_PERSON_DISTANCE = 0.5

_DURATION = safe_histogram(
    "workspace_photos_face_analysis_seconds",
    "Time spent analyzing one photo or video for faces.",
    labels=("backend", "media_type"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)
_RESULTS = safe_counter(
    "workspace_photos_face_analysis_total",
    "Photos and videos analyzed for faces, by outcome.",
    labels=("result", "media_type"),
)
_DETECTED = safe_counter(
    "workspace_photos_faces_detected_total",
    "Faces stored by the face analysis.",
)


def face_media_type(file_obj):
    """The kind of library item *file_obj* is when it can be read for faces:
    a photo, or a video where ffmpeg is installed. None otherwise."""
    media_type = media_type_for(file_obj)
    if media_type == MediaItem.MediaType.VIDEO and not video_faces_available():
        return None
    return media_type


def is_face_candidate(file_obj):
    """Whether *file_obj* is a photo or a video whose faces its owner wants found."""
    return (
        faces_available()
        and face_media_type(file_obj) is not None
        and file_obj.group_id is None
        and file_obj.deleted_at is None
        and not is_blocked(file_obj)
        and faces_enabled(file_obj.owner)
    )


def faces_catch_up_enabled():
    """The catch-up only queues work that can run: a misconfigured backend
    shows on the admin dashboard instead of failing every hour."""
    return faces_available() and not is_misconfigured()


def face_candidates(files):
    """The photos among *files*, and the videos where ffmpeg is installed.

    Without it a video is never pending: nothing could ever read it.
    """
    files = library_candidates(files)
    if not video_faces_available():
        files = files.filter(type__in=RASTER_LABELS)
    return files


def pending_faces_qs(*, reanalyze=False):
    """The personal photos and videos of opted-in users whose faces are
    missing or stale.

    Stale: other bytes, another backend, another owner, or an older version
    of this pipeline wrote them.
    """
    qs = exclude_blocked(
        face_candidates(File.objects.alive().with_blob()).filter(
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

    None means nothing was stored: the file is not a candidate, its blob
    could not be opened, the backend failed, or the file changed while it was
    read. A file that cannot be decoded is stored as analyzed with no face,
    so it does not come back every hour; so is one past the size limits.
    """
    if not is_face_candidate(file_obj):
        return None
    media_type = face_media_type(file_obj)
    backend = get_face_backend()
    started = time.monotonic()
    try:
        faces = _analyze(file_obj, media_type, backend)
    except Exception:
        _RESULTS.labels(result="error", media_type=media_type).inc()
        logger.exception("Face analysis failed for %s", scrub(file_obj.content.name))
        return None
    _DURATION.labels(backend=backend.key, media_type=media_type).observe(
        time.monotonic() - started
    )
    if faces is None:
        _RESULTS.labels(result="skipped", media_type=media_type).inc()
        return None
    _RESULTS.labels(
        result="faces" if faces else "no_faces", media_type=media_type
    ).inc()
    _DETECTED.inc(len(faces))

    from .face_grouping import assign_faces, maybe_queue_clustering

    assign_faces(file_obj.owner_id, [face.pk for face in faces])
    maybe_queue_clustering(file_obj.owner_id)
    return faces


def _analyze(file_obj, media_type, backend):
    read_hash = file_obj.content_hash
    owner_id = file_obj.owner_id
    if media_type == MediaItem.MediaType.VIDEO:
        found = _find_in_video(file_obj, backend)
    else:
        found = _find_in_photo(file_obj, backend)
    if found is None:
        return None

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
            if not lock_opt_in(owner_id):
                # Turned off while the models ran. The purge that follows
                # the setting runs after this commits, but it must find
                # nothing of this analysis to delete: none is written.
                _delete_crops(crops)
                return None
            faces = _replace_faces(file_obj, owner_id, found, media_type)
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


def _find_in_photo(file_obj, backend):
    if file_obj.size and file_obj.size > settings.PHOTOS_FACES_MAX_FILE_BYTES:
        return []
    image = _decode(file_obj)
    if image is False:
        return None
    if image is None:
        return []
    return [
        _Found(
            face=_face_row(file_obj, detection),
            embedding=detection.embedding,
            crop=detection.crop,
        )
        for detection in detect_faces(image, backend)
    ]


def _find_in_video(file_obj, backend):
    people = find_video_faces(file_obj, backend)
    if people is None:
        return None
    return [
        _Found(
            face=_face_row(file_obj, person.detection, timestamp=person.timestamp),
            embedding=person.embedding,
            crop=person.detection.crop,
        )
        for person in people
    ]


def _face_row(file_obj, detection, *, timestamp=None):
    width, height = detection.width, detection.height
    x, y, w, h = detection.box
    return Face(
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
        quality=round(detection.quality, 4),
        timestamp=timestamp,
    )


def _fraction(value, total):
    return min(1.0, max(0.0, float(value) / total))


def crop_path(owner_id, face_uuid):
    return f"faces/{owner_id}/{face_uuid}.webp"


def _replace_faces(file_obj, owner_id, found, media_type):
    """Swap the file's faces for *found*, keeping what the user decided.

    A new face of the same person as an old one inherits its cluster, its
    assignment and its rejection, and becomes the cover in its place: in a
    photo, the face at the same place (same box, give or take); in a video,
    the person whose embedding is that close, whichever frame shows them.
    Called inside the analysis transaction.
    """
    old_faces = list(Face.objects.filter(file_id=file_obj.pk))
    cover_of = dict(
        FaceCluster.objects.filter(cover__in=old_faces).values_list("cover_id", "pk")
    )
    if media_type == MediaItem.MediaType.VIDEO:
        inherited = _match_old_people(old_faces, found)
    else:
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
    return _pair(
        old_faces,
        new_faces,
        [[_iou(old, new) for new in new_faces] for old in old_faces],
        _SAME_FACE_IOU,
    )


def _match_old_people(old_faces, found):
    """{new face: old face} for the pairs whose embeddings are close enough."""
    from .face_grouping import max_distance

    new_vectors = [_unit(item.embedding) for item in found]
    similarity = []
    for old in old_faces:
        vector = (
            _unit(from_bytes(old.embedding, FACE_EMBEDDINGS.dims))
            if old.embedding
            else None
        )
        similarity.append(
            [-1.0 if vector is None else float(vector @ new) for new in new_vectors]
        )
    return _pair(
        old_faces,
        [item.face for item in found],
        similarity,
        1 - max_distance() * _SAME_PERSON_DISTANCE,
    )


def _pair(old_faces, new_faces, similarity, minimum):
    """{new face: old face}, the most similar pairs first, each face once.

    *similarity* is indexed [old][new]; a pair under *minimum* is no match.
    """
    pairs = sorted(
        (
            (similarity[i][j], i, j)
            for i in range(len(old_faces))
            for j in range(len(new_faces))
        ),
        reverse=True,
    )
    matched, used_old, used_new = {}, set(), set()
    for score, i, j in pairs:
        if score < minimum:
            break
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        matched[new_faces[j]] = old_faces[i]
    return matched


def _unit(vector):
    if vector is None:
        return None
    vector = np.asarray(vector, dtype=np.float64)
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else None


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
    """Drop the faces and the analysis of a file that left its owner's library."""
    from .face_grouping import refresh_clusters

    with transaction.atomic():
        faces = list(Face.objects.filter(file_id=file_obj.pk))
        clusters = {face.cluster_id for face in faces if face.cluster_id}
        for face in faces:
            face.delete()
        FaceAnalysis.objects.filter(file_id=file_obj.pk).delete()
    refresh_clusters(clusters)

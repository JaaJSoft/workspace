"""Find the people of a video: sample frames, find their faces, follow them.

ffmpeg samples the frames at scene changes and at a regular floor, decoded at
PHOTOS_FACES_VIDEO_DECODE_SIZE, and hands them over one at a time. Each
frame's faces are found like a photo's (face_images.py) and chained into
tracks (face_tracking.py). A track yields one face: the grouping then sees
each person once per video, as it sees them once per photo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from django.conf import settings

from workspace.common.logging import scrub
from workspace.common.metrics import safe_histogram
from workspace.files.services import ffmpeg

from .face_grouping import max_distance
from .face_images import Detection, detect_faces
from .face_tracking import Tracker

logger = logging.getLogger(__name__)

# Far above what the frame budget takes on an ordinary video, far below the
# Celery time limit.
_SAMPLING_TIMEOUT = 15 * 60
# ffmpeg's scene score above which a frame starts a new shot.
_SCENE_CHANGE = 0.3

_FRAMES = safe_histogram(
    "workspace_photos_video_face_frames",
    "Frames sampled from one video for face analysis.",
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120),
)
_TRACKS = safe_histogram(
    "workspace_photos_video_face_tracks",
    "People (face tracks) found in one video.",
    buckets=(0, 1, 2, 3, 5, 10, 20, 40),
)


@dataclass
class VideoFace:
    """One person of a video."""

    # The best face of the person's track: its box, landmarks, crop, quality.
    detection: Detection
    # The track's: the quality-weighted mean of its best faces.
    embedding: np.ndarray
    # Seconds into the video of the frame *detection* comes from.
    timestamp: float | None


def video_faces_available():
    """Whether this deployment can read videos for faces at all."""
    return bool(ffmpeg.FFMPEG and ffmpeg.FFPROBE)


def find_video_faces(file_obj, backend):
    """The people of the video *file_obj*, one VideoFace each.

    None when the blob could not be opened (the catch-up tries again). A video
    too large or too long to read, or one ffmpeg cannot decode, has no face:
    it is recorded as analyzed, like a photo that cannot be.
    """
    if file_obj.size and file_obj.size > settings.PHOTOS_FACES_VIDEO_MAX_FILE_BYTES:
        return []
    try:
        with ffmpeg.local_path(
            file_obj.content, max_bytes=settings.PHOTOS_FACES_VIDEO_MAX_FILE_BYTES
        ) as path:
            return _read(path, backend)
    except ffmpeg.MediaToolError as exc:
        logger.info(
            "Face analysis could not read the video %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return []
    except OSError as exc:
        logger.warning(
            "Face analysis cannot read the blob of %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None


def _read(path, backend):
    report = ffmpeg.probe(path)
    duration = ffmpeg.duration(report)
    if duration is not None and duration > settings.PHOTOS_FACES_VIDEO_MAX_DURATION:
        return []
    shown = ffmpeg.display_size(report)
    if shown is None:
        return []
    width, height = ffmpeg.fit(shown, settings.PHOTOS_FACES_VIDEO_DECODE_SIZE)
    max_frames = settings.PHOTOS_FACES_VIDEO_MAX_FRAMES
    # Half the budget covers the whole video at a regular pace, the other
    # half is left for the scene changes.
    interval = max(
        settings.PHOTOS_FACES_VIDEO_INTERVAL, 2 * (duration or 0) / max_frames
    )
    tracker = Tracker(max_distance())

    def on_frame(index, pixels):
        image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 3)
        tracker.add(index, detect_faces(image, backend))

    times = ffmpeg.sample_frames(
        path,
        on_frame,
        size=(width, height),
        interval=interval,
        max_frames=max_frames,
        scene=_SCENE_CHANGE,
        timeout=_SAMPLING_TIMEOUT,
    )
    start = ffmpeg.start_time(report)
    tracks = tracker.tracks()[: settings.PHOTOS_FACES_MAX_PER_PHOTO]
    _FRAMES.observe(len(times))
    _TRACKS.observe(len(tracks))
    faces = []
    for track in tracks:
        best = track.best()
        faces.append(
            VideoFace(
                detection=best.detection,
                embedding=track.embedding(),
                timestamp=(
                    round(max(0.0, times[best.frame] - start), 3)
                    if best.frame < len(times)
                    else None
                ),
            )
        )
    return faces

"""Find the people of a video: sample frames, find their faces, follow them.

The frames are read by video_frames.py at PHOTOS_FACES_VIDEO_DECODE_SIZE,
under the face budget (PHOTOS_FACES_VIDEO_*), and handed over one at a time.
Each frame's faces are found like a photo's (face_images.py) and chained into
tracks (face_tracking.py). A track yields one face: the grouping then sees
each person once per video, as it sees them once per photo.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from django.conf import settings

from workspace.common.metrics import safe_histogram

from .face_grouping import max_distance
from .face_images import Detection, detect_faces
from .face_tracking import Tracker
from .video_frames import FrameBudget, stream_frames

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
    # Seconds into the video of the frame *detection* comes from. Never None:
    # a face with a timestamp is what a video's face is.
    timestamp: float


def find_video_faces(file_obj, backend):
    """The people of the video *file_obj*, one VideoFace each.

    None when the blob could not be opened (the catch-up tries again). A video
    too large or too long to read, or one ffmpeg cannot decode, has no face:
    it is recorded as analyzed, like a photo that cannot be.
    """
    tracker = Tracker(max_distance())

    def on_frame(index, image):
        tracker.add(index, detect_faces(image, backend))

    times = stream_frames(file_obj, _budget(), on_frame, reader="Face analysis")
    if times is None:
        return None
    tracks = tracker.tracks()[: settings.PHOTOS_FACES_MAX_PER_PHOTO]
    if times:
        _FRAMES.observe(len(times))
        _TRACKS.observe(len(tracks))
    faces = []
    for track in tracks:
        best = track.best()
        faces.append(
            VideoFace(
                detection=best.detection,
                embedding=track.embedding(),
                timestamp=times[best.frame] if best.frame < len(times) else 0.0,
            )
        )
    return faces


def _budget():
    return FrameBudget(
        size=settings.PHOTOS_FACES_VIDEO_DECODE_SIZE,
        max_frames=settings.PHOTOS_FACES_VIDEO_MAX_FRAMES,
        min_interval=settings.PHOTOS_FACES_VIDEO_INTERVAL,
        max_file_bytes=settings.PHOTOS_FACES_VIDEO_MAX_FILE_BYTES,
        max_duration=settings.PHOTOS_FACES_VIDEO_MAX_DURATION,
    )

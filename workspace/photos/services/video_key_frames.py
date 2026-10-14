"""What a video shows, moment by moment: the frames read for its embeddings
(photo_embeddings.py) and its sensitive-content check (sensitive_photos.py).

ffmpeg reads the first frame, one at every scene change, and one at a regular
pace that spreads half of PHOTOS_VIDEO_KEY_FRAMES_MAX over the video: a phone
clip is one long shot, which no scene change would ever cut. The frames are
decoded at the size the model reads a photo at, and handed over once the
video has been read.

For the embeddings, a frame within the near-duplicate distance of one already
kept shows nothing new and is dropped: a still shot is one vector, not ten
alike, and a video ranks by the closest of the moments it shows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from PIL import Image

from workspace.common.logging import scrub
from workspace.common.metrics import safe_histogram
from workspace.files.services import ffmpeg

from .embedding.registry import duplicate_max_distance

logger = logging.getLogger(__name__)

# Far above what the frame budget takes on an ordinary video, far below the
# Celery time limit.
_SAMPLING_TIMEOUT = 15 * 60
# ffmpeg's scene score above which a frame starts a new shot.
_SCENE_CHANGE = 0.3
# The regular pace never samples more often: in a clip of a few seconds,
# frames closer than this show the same thing.
_MIN_INTERVAL = 2.0

_FRAMES = safe_histogram(
    "workspace_photos_video_key_frames",
    "Key frames embedded for one video.",
    buckets=(0, 1, 2, 3, 5, 8, 12, 20, 30),
)


@dataclass(frozen=True)
class KeyFrame:
    # Seconds into the video, as a player counts them.
    timestamp: float
    vector: np.ndarray
    # The frame as decoded, at the backend's decode_size.
    image: Image.Image


def video_frames_available():
    """Whether this deployment can read the frames of a video at all."""
    return bool(ffmpeg.FFMPEG and ffmpeg.FFPROBE)


def embed_key_frames(file_obj, backend):
    """The key frames of the video *file_obj* embedded by *backend*, in order.

    None when the blob could not be opened (the catch-up tries again). A video
    too large or too long to read, or one ffmpeg cannot decode, has none.
    Raises what the backend raises: the video stays pending.
    """
    frames = read_frames(file_obj, backend.decode_size)
    if frames is None:
        return None
    same = duplicate_max_distance()
    kept = []
    for timestamp, image in frames:
        vector = np.asarray(backend.embed_image(image), dtype=np.float64)
        if not any(_distance(vector, frame.vector) <= same for frame in kept):
            kept.append(KeyFrame(timestamp=timestamp, vector=vector, image=image))
    _FRAMES.observe(len(kept))
    return kept


def _distance(a, b):
    norms = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 1.0 - float(a @ b) / norms if norms else 2.0


def read_frames(file_obj, size):
    """``(timestamp, PIL image)`` for each frame read from the video
    *file_obj*, at most *size* on a side, in order.

    None when the blob could not be opened (the catch-up tries again). A video
    too large or too long to read, or one ffmpeg cannot decode, has none.
    """
    max_bytes = settings.PHOTOS_VIDEO_KEY_FRAMES_MAX_FILE_BYTES
    if file_obj.size and file_obj.size > max_bytes:
        return []
    try:
        with ffmpeg.local_path(file_obj.content, max_bytes=max_bytes) as path:
            return _read(path, size)
    except ffmpeg.MediaToolError as exc:
        logger.info(
            "Could not read the frames of %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return []
    except OSError as exc:
        logger.warning(
            "Cannot read the blob of the video %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None


def _read(path, size):
    report = ffmpeg.probe(path)
    duration = ffmpeg.duration(report)
    if (
        duration is not None
        and duration > settings.PHOTOS_VIDEO_KEY_FRAMES_MAX_DURATION
    ):
        return []
    shown = ffmpeg.display_size(report)
    if shown is None:
        return []
    width, height = ffmpeg.fit(shown, size)
    max_frames = settings.PHOTOS_VIDEO_KEY_FRAMES_MAX
    images = []

    def on_frame(index, pixels):
        images.append(Image.frombytes("RGB", (width, height), pixels))

    times = ffmpeg.sample_frames(
        path,
        on_frame,
        size=(width, height),
        interval=max(_MIN_INTERVAL, 2 * (duration or 0) / max_frames),
        max_frames=max_frames,
        scene=_SCENE_CHANGE,
        timeout=_SAMPLING_TIMEOUT,
    )
    start = ffmpeg.start_time(report)
    # A frame whose time ffmpeg did not report could not be opened at it.
    return [
        (round(max(0.0, at - start), 3), image)
        for at, image in zip(times, images, strict=False)
    ]

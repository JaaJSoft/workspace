"""What a video shows, moment by moment: the frames read for its embeddings
(photo_embeddings.py) and its sensitive-content check (sensitive_photos.py).

The frames are read by video_frames.py under the key frame budget
(PHOTOS_VIDEO_KEY_FRAMES_*): the first frame, the scene changes, and a regular
pace over the video, as a phone clip is one long shot that no scene change
would ever cut. They are decoded at the size the model reads a photo at, and
handed over once the video has been read.

For the embeddings, a frame within the near-duplicate distance of one already
kept shows nothing new and is dropped: a still shot is one vector, not ten
alike, and a video ranks by the closest of the moments it shows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from django.conf import settings
from PIL import Image

from workspace.common.metrics import safe_histogram

from .embedding.registry import duplicate_max_distance
from .video_frames import FrameBudget, stream_frames

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
    images = []

    def on_frame(index, pixels):
        images.append(Image.fromarray(pixels))

    budget = FrameBudget(
        size=size,
        max_frames=settings.PHOTOS_VIDEO_KEY_FRAMES_MAX,
        min_interval=_MIN_INTERVAL,
        max_file_bytes=settings.PHOTOS_VIDEO_KEY_FRAMES_MAX_FILE_BYTES,
        max_duration=settings.PHOTOS_VIDEO_KEY_FRAMES_MAX_DURATION,
    )
    times = stream_frames(file_obj, budget, on_frame, reader="Key frames")
    if times is None:
        return None
    # A frame whose time ffmpeg did not report could not be opened at it.
    return list(zip(times, images, strict=False))

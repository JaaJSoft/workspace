"""Read the frames of a library video for a model: what the face analysis
(face_video.py) and the key frames (video_key_frames.py) share.

ffmpeg takes a frame at the start, one at every scene change, and one at a
regular pace that spreads half the frame budget over the video, the other half
being left for the scene changes. The frames are decoded at the size the model
reads, displayed the way a player shows them, and handed over one at a time so
that only one is held in memory; their times come back once the video has been
read. Each caller brings its own FrameBudget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from workspace.common.logging import scrub
from workspace.files.services import ffmpeg

logger = logging.getLogger(__name__)

# Far above what a frame budget takes on an ordinary video, far below the
# Celery time limit.
_SAMPLING_TIMEOUT = 15 * 60
# ffmpeg's scene score above which a frame starts a new shot.
_SCENE_CHANGE = 0.3


@dataclass(frozen=True)
class FrameBudget:
    """What one pipeline reads of a video."""

    # Longest side, in px, of the frames handed over.
    size: int
    max_frames: int
    # The regular pace never takes frames closer than this, in seconds.
    min_interval: float
    # A video larger or longer (seconds) than this is not read at all.
    max_file_bytes: int
    max_duration: float


def video_frames_available():
    """Whether this deployment can read the frames of a video at all."""
    return bool(ffmpeg.FFMPEG and ffmpeg.FFPROBE)


def stream_frames(file_obj, budget, on_frame, *, reader):
    """Hand the frames of the video *file_obj* to ``on_frame(index, pixels)``,
    *pixels* an RGB uint8 array of (height, width, 3); return the time of each
    frame in seconds, as a player counts them, in index order.

    An empty list when the video is not read: past *budget*'s limits, or not
    one ffmpeg can decode. None when the blob could not be opened (the
    catch-up tries again). *reader* names the caller in the logs.
    """
    if file_obj.size and file_obj.size > budget.max_file_bytes:
        return []
    try:
        with ffmpeg.local_path(
            file_obj.content, max_bytes=budget.max_file_bytes
        ) as path:
            return _stream(path, budget, on_frame)
    except ffmpeg.MediaToolError as exc:
        logger.info(
            "%s could not read the video %s: %s",
            reader,
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return []
    except OSError as exc:
        logger.warning(
            "%s cannot read the blob of %s: %s",
            reader,
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None


def _stream(path, budget, on_frame):
    report = ffmpeg.probe(path)
    duration = ffmpeg.duration(report)
    if duration is not None and duration > budget.max_duration:
        return []
    shown = ffmpeg.display_size(report)
    if shown is None:
        return []
    width, height = ffmpeg.fit(shown, budget.size)

    def frame(index, pixels):
        on_frame(index, np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 3))

    times = ffmpeg.sample_frames(
        path,
        frame,
        size=(width, height),
        interval=max(budget.min_interval, 2 * (duration or 0) / budget.max_frames),
        max_frames=budget.max_frames,
        scene=_SCENE_CHANGE,
        timeout=_SAMPLING_TIMEOUT,
    )
    start = ffmpeg.start_time(report)
    return [round(max(0.0, at - start), 3) for at in times]

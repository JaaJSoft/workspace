"""Show a video attachment to a vision model as a series of still frames."""

import base64
import io
import logging

from PIL import Image

from workspace.common.logging import scrub
from workspace.files.services import ffmpeg

logger = logging.getLogger(__name__)

_VIDEO_MAX_FRAMES = 30  # cap frames sent to the model to limit context size
# Longest side of a frame, in px: past that a vision model scales it down
# anyway, and the request only grows.
_FRAME_MAX_SIDE = 1280
_JPEG_QUALITY = 75
_TIMEOUT = 120


def extract_video_frames(att):
    """Extract evenly-spaced frames from a video attachment (max _VIDEO_MAX_FRAMES).

    Returns (frame_parts, description) where frame_parts is a list of image_url
    content parts and description is a string summarising the video for the model.
    Returns ([], None) when ffmpeg is unavailable or the extraction fails so
    the caller can degrade gracefully without a full bot-response failure.
    """
    if not ffmpeg.FFMPEG:
        return [], None
    try:
        with ffmpeg.local_path(att.file) as path:
            return _extract(path, att)
    except ffmpeg.MediaToolError, OSError:
        logger.warning("Could not extract frames from video %s", scrub(att.uuid))
        return [], None


def _extract(path, att):
    report = ffmpeg.probe(path)
    duration = ffmpeg.duration(report)
    shown = ffmpeg.display_size(report)
    if shown is None:
        raise ffmpeg.MediaToolError("no video stream")
    size = ffmpeg.fit(shown, _FRAME_MAX_SIDE)
    # One frame a second, spread over the whole video once that would pass
    # the cap.
    interval = (
        duration / _VIDEO_MAX_FRAMES if duration and duration > _VIDEO_MAX_FRAMES else 1
    )
    parts = []

    def on_frame(index, pixels):
        buffer = io.BytesIO()
        Image.frombytes("RGB", size, pixels).save(
            buffer, format="JPEG", quality=_JPEG_QUALITY
        )
        b64 = base64.b64encode(buffer.getvalue()).decode()
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    ffmpeg.sample_frames(
        path,
        on_frame,
        size=size,
        interval=interval,
        max_frames=_VIDEO_MAX_FRAMES,
        timeout=_TIMEOUT,
    )
    if not parts:
        return [], None
    dur_str = f"{duration:.0f}s" if duration else "unknown duration"
    spacing = duration / len(parts) if duration else 1
    description = (
        f'The user attached a video: "{att.original_name}" '
        f"(duration: {dur_str}). Since you cannot watch videos directly, "
        f"it has been converted into {len(parts)} frames "
        f"(1 frame every {spacing:.1f}s) shown in chronological order "
        f"in the next message. Analyze these frames to understand "
        f"what happens in the video."
    )
    return parts, description

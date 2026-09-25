"""Run ffprobe and ffmpeg on the bytes of a stored file.

Both tools need a seekable input: an MP4 whose ``moov`` atom sits at the end
of the file cannot be read from a pipe. Local storage hands them the blob's
own path; any other backend gets a temporary copy, bounded in size.

The binaries are optional. Without them every call raises
:class:`MediaToolError`, and callers degrade: no poster frame, a video with no
metadata.
"""

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager

# Resolved once, from the deploy's PATH, so a later PATH change cannot
# redirect the subprocess calls. Read through the module (ffmpeg.FFPROBE) so a
# test can patch them away.
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

PROBE_TIMEOUT = 30
FRAME_TIMEOUT = 60

# A non-local storage backend has to copy the blob to disk before either tool
# can read it; past this size the copy costs more than the result is worth.
MAX_COPY_BYTES = 2 * 1024**3

_COPY_CHUNK = 1024 * 1024


class MediaToolError(Exception):
    """ffprobe or ffmpeg is missing, failed, or could not read the input."""


@contextmanager
def local_path(field_file, *, max_bytes=MAX_COPY_BYTES):
    """A filesystem path holding the bytes of *field_file*.

    Raises ``FileNotFoundError`` when the blob is missing from storage, and
    :class:`MediaToolError` when a copy would exceed *max_bytes*.
    """
    try:
        path = field_file.path
    except NotImplementedError:
        path = None
    if path is not None:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        yield path
        return

    if field_file.size > max_bytes:
        raise MediaToolError("the file is too large to copy for reading")
    with tempfile.NamedTemporaryFile(prefix="media-") as copy:
        with field_file.open("rb") as source:
            shutil.copyfileobj(source, copy, _COPY_CHUNK)
        copy.flush()
        yield copy.name


def _input_args(path):
    # The file: prefix and the whitelist keep ffmpeg on the local file: a
    # container cannot make it open a URL or another protocol it references.
    return ["-protocol_whitelist", "file", "-i", f"file:{path}"]


def probe(path, *, timeout=PROBE_TIMEOUT):
    """ffprobe's report on the container and the streams of the file at *path*."""
    if not FFPROBE:
        raise MediaToolError("ffprobe is not installed")
    command = [
        FFPROBE,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        *_input_args(path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout, check=True
        )
        report = json.loads(result.stdout or b"{}")
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise MediaToolError(str(exc)) from exc
    if not isinstance(report, dict):
        raise MediaToolError("ffprobe returned no report")
    return report


def duration(report):
    """The container duration in seconds from a :func:`probe` report, or None."""
    try:
        value = float(report.get("format", {}).get("duration"))
    except TypeError, ValueError:
        return None
    return value if value > 0 else None


def video_stream(report):
    """The first real video stream of a :func:`probe` report, or ``{}``.

    An embedded cover picture (an album art, a thumbnail track) is reported
    as a video stream too, and is not one.
    """
    for stream in _streams(report):
        if stream.get("codec_type") != "video":
            continue
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        return stream
    return {}


def audio_stream(report):
    """The first audio stream of a :func:`probe` report, or ``{}``."""
    for stream in _streams(report):
        if stream.get("codec_type") == "audio":
            return stream
    return {}


def _streams(report):
    return [s for s in report.get("streams") or [] if isinstance(s, dict)]


def extract_frame(path, *, at, max_size, timeout=FRAME_TIMEOUT):
    """One frame of the video at *path*, *at* seconds in, as PNG bytes.

    The frame is displayed the way a player shows it (ffmpeg applies the
    rotation the container carries) and fits within *max_size*. Empty bytes
    mean there was no frame at that point, typically a seek past the end.
    """
    if not FFMPEG:
        raise MediaToolError("ffmpeg is not installed")
    width, height = max_size
    command = [
        FFMPEG,
        "-nostdin",
        "-v",
        "error",
        # Before -i, the seek jumps to the nearest keyframe instead of
        # decoding everything up to it.
        "-ss",
        f"{max(at, 0):.3f}",
        *_input_args(path),
        "-frames:v",
        "1",
        "-vf",
        # Shrinks to fit, never enlarges: min() keeps a small video its size.
        f"scale=w='min({width},iw)':h='min({height},ih)'"
        ":force_original_aspect_ratio=decrease",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout, check=True
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise MediaToolError(str(exc)) from exc
    return result.stdout

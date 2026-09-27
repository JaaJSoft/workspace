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
import re
import shutil
import subprocess
import tempfile
import threading
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


def start_time(report):
    """Seconds before the first frame of a :func:`probe` report, 0 when unknown.

    A player counts from there: the frame at *t* in ffmpeg's clock is shown at
    ``t - start_time``.
    """
    try:
        value = float(report.get("format", {}).get("start_time"))
    except TypeError, ValueError:
        return 0.0
    return value if value > 0 else 0.0


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


def display_size(report):
    """(width, height) of the video of a :func:`probe` report as a player
    shows it, the rotation the container carries applied; None when unknown.
    """
    stream = video_stream(report)
    width, height = _positive(stream.get("width")), _positive(stream.get("height"))
    if not width or not height:
        return None
    if _rotation(stream) % 180 == 90:
        return height, width
    return width, height


def fit(size, max_side):
    """*size* shrunk to *max_side* on its longest side, never enlarged."""
    width, height = size
    scale = min(1.0, max_side / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _positive(value):
    return value if isinstance(value, int) and value > 0 else None


def _rotation(stream):
    """The rotation a player applies, in degrees, from the display matrix
    (current ffmpeg) or the ``rotate`` tag (older muxers)."""
    for side_data in stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            return _degrees(side_data["rotation"])
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return 0
    return _degrees({str(k).lower(): v for k, v in tags.items()}.get("rotate"))


def _degrees(value):
    try:
        return round(float(value)) % 360
    except TypeError, ValueError:
        return 0


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


_PTS_TIME = re.compile(rb"\bpts_time:\s*(-?[0-9.]+)")


def sample_frames(
    path,
    on_frame,
    *,
    size,
    interval,
    max_frames,
    scene=None,
    scene_gap=0.5,
    timeout=FRAME_TIMEOUT,
):
    """Hand frames of the video at *path* to *on_frame*; return their times.

    A frame is taken at the start, then whenever *interval* seconds have
    passed since the last one taken. With *scene*, one is also taken at every
    scene change (ffmpeg's scene score above *scene*) at least *scene_gap*
    seconds after the last one. At most *max_frames* are taken, from the
    start: the budget runs out before the end of a video with more scene
    changes than it allows.

    *on_frame* receives ``(index, pixels)``: RGB24 bytes of exactly *size*
    (width, height, see :func:`display_size` and :func:`fit`), displayed the
    way a player shows them. The returned list
    holds the time of each frame in ffmpeg's clock, in index order; it is only
    known once the video has been read, the frames stream while it decodes so
    that one of them at a time is held in memory.
    """
    if not FFMPEG:
        raise MediaToolError("ffmpeg is not installed")
    width, height = size
    selection = f"isnan(prev_selected_t)+gte(t-prev_selected_t,{interval:.3f})"
    if scene is not None:
        # The scene score compares every decoded frame with the one before:
        # only asked for when wanted.
        selection += f"+gt(scene,{scene})*gte(t-prev_selected_t,{scene_gap:.3f})"
    command = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        # showinfo reports each frame's time at the info level.
        "-v",
        "info",
        *_input_args(path),
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"select='{selection}',scale={width}:{height},showinfo",
        "-fps_mode",
        "vfr",
        "-frames:v",
        str(max_frames),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    frame_bytes = width * height * 3
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise MediaToolError(str(exc)) from exc
    times, tail = [], []
    # stderr is read on the side: a pipe left full would stall ffmpeg before
    # its next frame reached stdout.
    reader = threading.Thread(
        target=_read_log, args=(process.stderr, times, tail), daemon=True
    )
    reader.start()
    expired = threading.Event()

    def expire():
        expired.set()
        process.kill()

    timer = threading.Timer(timeout, expire)
    timer.start()
    index = 0
    finished = False
    try:
        while not expired.is_set():
            pixels = process.stdout.read(frame_bytes)
            if len(pixels) < frame_bytes:
                break
            on_frame(index, pixels)
            index += 1
        finished = True
    finally:
        timer.cancel()
        if not finished and process.poll() is None:
            process.kill()
        # Closed before waiting: whatever still writes to the pipe (the real
        # ffmpeg behind a wrapper script that was killed) gets a broken pipe
        # instead of blocking on it forever.
        process.stdout.close()
        returncode = process.wait()
        reader.join()
    if expired.is_set():
        raise MediaToolError(f"reading the video took more than {timeout} s")
    if returncode != 0:
        raise MediaToolError(
            b"\n".join(tail).decode(errors="replace") or "ffmpeg failed"
        )
    return times[:index]


def _read_log(stream, times, tail):
    for line in stream:
        if b"Parsed_showinfo" in line:
            match = _PTS_TIME.search(line)
            if match is not None:
                times.append(float(match[1]))
            continue
        tail.append(line.rstrip())
        del tail[:-5]
    stream.close()

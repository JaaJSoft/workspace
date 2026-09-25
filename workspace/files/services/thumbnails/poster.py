"""Poster frames: the still a video shows before it plays."""

from io import BytesIO

from workspace.files.services import ffmpeg

# About a second in, past the black or faded-in opening most clips start
# with. A clip shorter than ten seconds takes its frame at 10% of its length
# instead, so a two-second clip is not read at its very last frames.
POSTER_OFFSET = 1.0
SHORT_CLIP_FRACTION = 0.1


def poster_offset(duration):
    """Where to take the poster frame of a clip *duration* seconds long."""
    if duration is None:
        return POSTER_OFFSET
    return min(POSTER_OFFSET, duration * SHORT_CLIP_FRACTION)


def poster_frame(file_obj, max_size):
    """The poster frame of *file_obj* as a Pillow image fitting *max_size*.

    Raises :class:`ffmpeg.MediaToolError` when no frame can be read, and
    ``FileNotFoundError`` when the blob is missing from storage.
    """
    from PIL import Image

    with ffmpeg.local_path(file_obj.content) as path:
        try:
            clip_duration = ffmpeg.duration(ffmpeg.probe(path))
        except ffmpeg.MediaToolError:
            clip_duration = None
        at = poster_offset(clip_duration)
        png = ffmpeg.extract_frame(path, at=at, max_size=max_size)
        if not png and at > 0:
            # A container that misreports its duration: the first frame is
            # better than none.
            png = ffmpeg.extract_frame(path, at=0, max_size=max_size)
    if not png:
        raise ffmpeg.MediaToolError("the video has no frame to show")
    return Image.open(BytesIO(png))

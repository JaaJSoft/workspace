"""What an album serves someone it does not let download: no original.

An original carries its metadata, the place it was taken included, and an
inline response is as good as a download to whoever receives it. A viewer
without download rights, a public link above all, gets a rendition instead:
a photo re-encoded to WebP with nothing but its pixels (orientation applied,
``PREVIEW_MAX_SIDE`` at most), a video remuxed without its metadata nor its
data tracks, bytes untouched.

Renditions are kept in storage next to nothing else, keyed by the file's
content hash, so the work is done once per version; a new version replaces
the old rendition, and a file leaving for good takes its renditions along
(``signals.drop_renditions``).
"""

import logging
import os
import subprocess
import tempfile
from io import BytesIO

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from workspace.common.logging import scrub
from workspace.files.services import ffmpeg
from workspace.photos.models import MediaItem

logger = logging.getLogger(__name__)

PREVIEW_MAX_SIDE = 2560
PREVIEW_QUALITY = 85
REMUX_TIMEOUT = 300

RENDITIONS_ROOT = "photos/renditions"

# The demuxers a remux may read with: video containers alone. A playlist
# demuxer (HLS, concat) would follow the references a crafted upload holds,
# local files included, and the remux would hand their bytes back.
DEMUXERS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi"

# What a remux writes, by the file's type label, as (extension, muxer,
# content type): fixed here rather than read off the file's own name, which
# its uploader chose.
CONTAINERS = {
    "mp4": (".mp4", "mp4", "video/mp4"),
    "qt": (".mov", "mov", "video/quicktime"),
    "3gp": (".mp4", "mp4", "video/mp4"),
    "webm": (".webm", "webm", "video/webm"),
}
# Matroska takes whatever codec the others refuse.
DEFAULT_CONTAINER = (".mkv", "matroska", "video/x-matroska")


class RenditionUnavailable(Exception):
    """The file could not be turned into a rendition: undecodable, or a
    video on a deployment without ffmpeg. Nothing is served instead."""


class RenditionBusy(Exception):
    """Another request is making this very rendition right now."""


def renditions_dir(file_uuid):
    return f"{RENDITIONS_ROOT}/{file_uuid}"


def _path(file_obj, extension):
    version = file_obj.content_hash or "unhashed"
    return f"{renditions_dir(file_obj.uuid)}/{version}{extension}"


def _store(file_obj, path, content):
    """Keep *content* at *path*, dropping the renditions of older versions."""
    directory = renditions_dir(file_obj.uuid)
    try:
        _, names = default_storage.listdir(directory)
    except OSError:
        names = []
    for name in names:
        stale = f"{directory}/{name}"
        if stale != path:
            default_storage.delete(stale)
    if not default_storage.exists(path):
        default_storage.save(path, content)


def _photo(file_obj):
    from PIL import Image, ImageOps

    with file_obj.content.open("rb") as handle:
        img = Image.open(handle)
        img.draft(None, (PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode or img.mode == "P" else "RGB")
        img.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE), Image.LANCZOS)
        out = BytesIO()
        # No exif= nor icc_profile= argument: the WebP carries pixels alone.
        img.save(out, format="WEBP", quality=PREVIEW_QUALITY)
    return ContentFile(out.getvalue())


def _video(file_obj, extension, muxer):
    if ffmpeg.FFMPEG is None:
        raise RenditionUnavailable("ffmpeg is not installed")
    with ffmpeg.local_path(file_obj.content) as source:
        with tempfile.TemporaryDirectory(prefix="rendition-") as work:
            target = os.path.join(work, f"out{extension}")
            command = [
                ffmpeg.FFMPEG,
                "-v",
                "error",
                "-format_whitelist",
                DEMUXERS,
                *ffmpeg._input_args(source),
                # Picture and sound only: a phone's timed metadata track
                # holds the location too.
                "-map",
                "0:v",
                "-map",
                "0:a?",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                muxer,
                "-y",
                f"file:{target}",
            ]
            subprocess.run(
                command, check=True, capture_output=True, timeout=REMUX_TIMEOUT
            )
            with open(target, "rb") as remuxed:
                return ContentFile(remuxed.read())


def rendition(file_obj):
    """The path in storage of *file_obj*'s rendition, made when missing, and
    its content type.

    Raises RenditionUnavailable when none can be made, and RenditionBusy
    while another request is making it: one at a time per file version, so
    a burst of visitors on a fresh link costs one remux, not one each.
    """
    from PIL import Image

    media_type = getattr(getattr(file_obj, "media_item", None), "media_type", None)
    if media_type == MediaItem.MediaType.VIDEO:
        extension, muxer, content_type = CONTAINERS.get(
            file_obj.type, DEFAULT_CONTAINER
        )
    else:
        extension, muxer, content_type = ".webp", None, "image/webp"
    path = _path(file_obj, extension)
    if default_storage.exists(path):
        return path, content_type
    lock = f"photos:rendition:{path}"
    if not cache.add(lock, 1, timeout=REMUX_TIMEOUT + 60):
        raise RenditionBusy(path)
    try:
        if muxer is not None:
            content = _video(file_obj, extension, muxer)
        else:
            content = _photo(file_obj)
        _store(file_obj, path, content)
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
        subprocess.SubprocessError,
        ffmpeg.MediaToolError,
    ) as exc:
        logger.warning(
            "Could not make a rendition of %s: %s",
            scrub(file_obj.uuid),
            scrub(type(exc).__name__),
        )
        raise RenditionUnavailable(type(exc).__name__) from exc
    finally:
        cache.delete(lock)
    return path, content_type


def drop_renditions(file_uuid):
    """Delete every rendition of the file *file_uuid*."""
    directory = renditions_dir(file_uuid)
    try:
        _, names = default_storage.listdir(directory)
    except OSError:
        return
    for name in names:
        try:
            default_storage.delete(f"{directory}/{name}")
        except OSError:
            logger.warning("Could not delete a rendition of %s", scrub(file_uuid))

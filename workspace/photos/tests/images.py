"""Fixtures for the photos tests: tiny pictures with chosen EXIF, and videos."""

import io

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import ExifTags, Image

from workspace.files.models import MediaInfo
from workspace.files.services import FileService
from workspace.files.tests.videos import clip_bytes
from workspace.photos.models import MediaItem


def jpeg_bytes(
    *,
    size=(64, 48),
    taken=None,
    offset=None,
    orientation=None,
    make=None,
    model=None,
    color=(200, 60, 40),
):
    """A JPEG carrying exactly the EXIF tags that were asked for."""
    exif = Image.Exif()
    if make is not None:
        exif[ExifTags.Base.Make] = make
    if model is not None:
        exif[ExifTags.Base.Model] = model
    if orientation is not None:
        exif[ExifTags.Base.Orientation] = orientation
    if taken is not None or offset is not None:
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        if taken is not None:
            exif_ifd[ExifTags.Base.DateTimeOriginal] = taken
        if offset is not None:
            exif_ifd[ExifTags.Base.OffsetTimeOriginal] = offset
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def png_bytes(size=(32, 32)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def upload(owner, name, data=None, *, parent=None):
    """A file created the way an upload creates it (CREATED event included)."""
    data = jpeg_bytes() if data is None else data
    return FileService.create_file(
        owner=owner,
        name=name,
        parent=parent,
        content=ContentFile(data, name=name),
    )


def make_photo(owner, name, taken_at, *, parent=None, **fields):
    """An uploaded JPEG with its MediaItem row written directly, for listing tests.

    Outside a TestCase transaction the upload's own handler may already have
    written the row, so this overwrites it rather than inserting.
    """
    file_obj = upload(owner, name, parent=parent)
    MediaItem.objects.update_or_create(
        file=file_obj,
        defaults={
            "taken_at": taken_at,
            "content_hash": file_obj.content_hash,
            "analyzed_at": timezone.now(),
            **fields,
        },
    )
    return file_obj


def make_video(
    owner,
    name,
    taken_at,
    *,
    clip="clip.webm",
    duration=3.0,
    video_codec="vp9",
    parent=None,
    **fields,
):
    """An uploaded clip (see files/tests/videos.py) with its MediaItem and
    MediaInfo rows written directly."""
    file_obj = upload(owner, name, clip_bytes(clip), parent=parent)
    MediaItem.objects.update_or_create(
        file=file_obj,
        defaults={
            "media_type": MediaItem.MediaType.VIDEO,
            "taken_at": taken_at,
            "content_hash": file_obj.content_hash,
            "analyzed_at": timezone.now(),
            **fields,
        },
    )
    MediaInfo.objects.update_or_create(
        file=file_obj,
        defaults={
            "duration": duration,
            "video_codec": video_codec,
            "content_hash": file_obj.content_hash,
            "probed_at": timezone.now(),
        },
    )
    return file_obj

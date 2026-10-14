"""Shared setup for the embedding tests: the fake backend, the feature on."""

from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from PIL import Image

from workspace.photos.models import MediaItem
from workspace.photos.services import photo_embeddings
from workspace.photos.services.analysis import ANALYSIS_VERSIONS
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend, colour_vector
from workspace.photos.services.photo_embeddings import embed_file
from workspace.photos.services.video_key_frames import KeyFrame

from .images import jpeg_bytes, make_video, upload

embeddings_on = override_settings(
    PHOTOS_EMBEDDINGS_ENABLED=True,
    PHOTOS_EMBEDDING_BACKEND="fake",
    PHOTOS_SIMILAR_MAX_DISTANCE=None,
    PHOTOS_DUPLICATE_MAX_DISTANCE=None,
    PHOTOS_DESCRIPTION_MAX_DISTANCE=None,
)


def ffmpeg_installed(installed=True):
    """ffmpeg and ffprobe said to be installed, or not: for the tests that
    never decode a video (those that do are marked requires_ffmpeg)."""
    tool = "ffmpeg" if installed else None
    return patch.multiple("workspace.files.services.ffmpeg", FFMPEG=tool, FFPROBE=tool)


RED = (255, 0, 0)
DARK_RED = (140, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)


def library_photo(owner, name, colour, *, parent=None, embed=True):
    """A photo of one *colour* (see embedding/fake.py) in the library: its
    MediaItem row written, and embedded unless *embed* is False."""
    photo = upload(owner, name, jpeg_bytes(color=colour), parent=parent)
    MediaItem.objects.update_or_create(
        file=photo,
        defaults={
            "content_hash": photo.content_hash,
            "analysis_version": ANALYSIS_VERSIONS[MediaItem.MediaType.PHOTO],
            "analyzed_at": timezone.now(),
        },
    )
    if embed:
        embed_file(photo)
    return photo


def library_video(owner, name, moments, *, parent=None):
    """A video in the library whose key frames show each ``(timestamp,
    colour)`` of *moments*, embedded without decoding it: no ffmpeg needed.
    """
    video = make_video(owner, name, None, parent=parent)
    dims = FakeEmbeddingBackend.dims
    frames = [
        KeyFrame(
            timestamp=timestamp,
            vector=colour_vector(colour, dims),
            image=Image.new("RGB", (16, 9), colour),
        )
        for timestamp, colour in moments
    ]
    with (
        ffmpeg_installed(),
        patch.object(photo_embeddings, "embed_key_frames", return_value=frames),
    ):
        embed_file(video)
    return video

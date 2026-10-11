"""Shared setup for the embedding tests: the fake backend, the feature on."""

from django.test import override_settings
from django.utils import timezone

from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import ANALYSIS_VERSIONS
from workspace.photos.services.photo_embeddings import embed_photo

from .images import jpeg_bytes, upload

embeddings_on = override_settings(
    PHOTOS_EMBEDDINGS_ENABLED=True,
    PHOTOS_EMBEDDING_BACKEND="fake",
    PHOTOS_SIMILAR_MAX_DISTANCE=None,
    PHOTOS_DUPLICATE_MAX_DISTANCE=None,
    PHOTOS_DESCRIPTION_MAX_DISTANCE=None,
)

RED = (255, 0, 0)
DARK_RED = (140, 0, 0)
BLUE = (0, 0, 255)


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
        embed_photo(photo)
    return photo

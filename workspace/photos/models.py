from django.db import models

from workspace.common.uuids import uuid_v7_or_v4
from workspace.files.models import File


class MediaItem(models.Model):
    """What the photo library knows about one photo or video file.

    Written off-request, after the file's content lands (see
    ``services/handlers.py``) or by the hourly catch-up. A file without a row
    has simply not been analyzed yet: there is no pending status, and the
    upload path never writes here.
    """

    class MediaType(models.TextChoices):
        PHOTO = "photo", "Photo"
        VIDEO = "video", "Video"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    file = models.OneToOneField(
        File, on_delete=models.CASCADE, related_name="media_item"
    )
    media_type = models.CharField(
        max_length=8, choices=MediaType.choices, default=MediaType.PHOTO
    )
    # When the picture or the recording was taken: EXIF DateTimeOriginal for a
    # photo, the container's creation date for a video (see services/exif.py
    # and services/video.py for how each is read). Null when the file carries
    # no date: the upload date is a different fact and never stands in.
    taken_at = models.DateTimeField(null=True, blank=True)
    # As displayed, i.e. with the EXIF orientation or the video rotation
    # applied. Null when the file could not be decoded.
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    camera_make = models.CharField(max_length=128, blank=True, default="")
    camera_model = models.CharField(max_length=128, blank=True, default="")
    # Signed decimal degrees, where the file says it was taken.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    # The file's content_hash when these values were read. A row whose hash
    # no longer matches the file describes bytes that were replaced, and the
    # catch-up task reads the file again.
    content_hash = models.CharField(max_length=64, blank=True, default="")
    analyzed_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["taken_at"], name="media_item_taken_at_idx"),
            models.Index(
                fields=["latitude", "longitude"], name="media_item_location_idx"
            ),
        ]

    def __str__(self):
        return f"MediaItem: {self.file_id}"

from django.db import models

from workspace.common.uuids import uuid_v7_or_v4
from workspace.files.models import File


class MediaItem(models.Model):
    """What the photo library knows about one raster image file.

    Written off-request, after the file's content lands (see
    ``services/handlers.py``) or by the hourly catch-up. A file without a row
    has simply not been analyzed yet: there is no pending status, and the
    upload path never writes here.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    file = models.OneToOneField(
        File, on_delete=models.CASCADE, related_name="media_item"
    )
    # EXIF DateTimeOriginal. With OffsetTimeOriginal it is the exact instant;
    # without it the wall-clock reading is taken in the owner's timezone, so
    # the photo lands on the day it shows. Null when the image carries no
    # capture date: the upload date is a different fact and never stands in.
    taken_at = models.DateTimeField(null=True, blank=True)
    # As displayed, i.e. with the EXIF orientation applied. Null when the
    # image could not be decoded.
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    camera_make = models.CharField(max_length=128, blank=True, default="")
    camera_model = models.CharField(max_length=128, blank=True, default="")
    # The file's content_hash when these values were read. A row whose hash
    # no longer matches the file describes bytes that were replaced, and the
    # catch-up task reads the file again.
    content_hash = models.CharField(max_length=64, blank=True, default="")
    analyzed_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["taken_at"], name="media_item_taken_at_idx"),
        ]

    def __str__(self):
        return f"MediaItem: {self.file_id}"

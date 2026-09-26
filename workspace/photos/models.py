from django.conf import settings
from django.db import models
from django.utils import timezone

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
    # The shooting settings, read from a photo's EXIF (see services/exif.py);
    # a video leaves them null. Null also means "not in the file": no value
    # ever stands in for a missing one.
    lens_make = models.CharField(max_length=128, null=True, blank=True)
    lens_model = models.CharField(max_length=128, null=True, blank=True)
    # Millimetres: the lens's own, and the full-frame equivalent the camera
    # computed for its sensor.
    focal_length = models.FloatField(null=True, blank=True)
    focal_length_35mm = models.PositiveIntegerField(null=True, blank=True)
    f_number = models.FloatField(null=True, blank=True)
    # Seconds.
    exposure_time = models.FloatField(null=True, blank=True)
    iso = models.PositiveIntegerField(null=True, blank=True)
    # EV, signed.
    exposure_bias = models.FloatField(null=True, blank=True)
    flash_fired = models.BooleanField(null=True, blank=True)
    # Signed decimal degrees, where the file says it was taken.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    # Metres above sea level, negative below it. Only kept with a position.
    altitude = models.FloatField(null=True, blank=True)
    # The file's content_hash when these values were read. A row whose hash
    # no longer matches the file describes bytes that were replaced, and the
    # catch-up task reads the file again.
    content_hash = models.CharField(max_length=64, blank=True, default="")
    # The version of the reader that wrote the row (ANALYSIS_VERSIONS in
    # services/analysis.py). A row written by an older one is missing fields
    # the current reader fills, and the catch-up task reads the file again.
    # Null for rows written before versions were recorded.
    analysis_version = models.PositiveSmallIntegerField(null=True, blank=True)
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


class Album(models.Model):
    """A titled collection of library photos and videos.

    Items point at ``File`` rows and are never copies: a photo can sit in any
    number of albums without moving in Files, and it stays its owner's. What a
    viewer sees of an album is always narrowed to the files they can open
    (see ``queries.album_files``).

    ``owner`` and ``group`` mirror ``File.owner`` / ``File.group``: a personal
    album has no group, a group album belongs to that group's members and
    ``owner`` records who created it.
    """

    class SortMode(models.TextChoices):
        CAPTURE_DATE = "capture_date", "Capture date"
        MANUAL = "manual", "Manual"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="photo_albums",
    )
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="photo_albums",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # The file the owner picked. It is only shown while it is an item of the
    # album the viewer can see; otherwise the cover falls back to the most
    # recently added visible item (see ``queries.album_summaries``).
    cover = models.ForeignKey(
        File,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    sort_mode = models.CharField(
        max_length=16, choices=SortMode.choices, default=SortMode.CAPTURE_DATE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "group"], name="photo_album_owner_idx"),
            models.Index(fields=["group"], name="photo_album_group_idx"),
        ]

    def __str__(self):
        return f"Album: {self.title}"


class AlbumItem(models.Model):
    """One file in one album.

    ``position`` is sparse (see ``services.albums.POSITION_GAP``): adding an
    item or moving a few writes those rows alone, so several people adding
    at once never rewrite each other's order. Two rows may share a position
    after a race; the order breaks the tie on the file's uuid.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="items")
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="album_items")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    added_at = models.DateTimeField(default=timezone.now)
    position = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["album", "file"], name="photo_album_item_unique_file"
            ),
        ]
        indexes = [
            models.Index(fields=["album", "position"], name="photo_album_item_pos_idx"),
        ]

    def __str__(self):
        return f"AlbumItem: {self.file_id} in {self.album_id}"

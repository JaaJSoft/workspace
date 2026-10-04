"""Someone an album does not let download never receives an original: its
photos come re-encoded without metadata, its videos remuxed without it."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import ExifTags, Image

from workspace.files.models import File
from workspace.files.services import ffmpeg
from workspace.photos.models import AlbumShare, MediaItem
from workspace.photos.services.album_renditions import (
    PREVIEW_MAX_SIDE,
    RenditionUnavailable,
    drop_renditions,
    rendition,
)
from workspace.photos.services.album_sharing import create_link, share_album
from workspace.photos.services.albums import add_items, create_album

from .images import jpeg_bytes, make_video, upload

User = get_user_model()

GPS = {
    ExifTags.GPS.GPSLatitudeRef: "N",
    ExifTags.GPS.GPSLatitude: (48.0, 51.0, 30.0),
    ExifTags.GPS.GPSLongitudeRef: "E",
    ExifTags.GPS.GPSLongitude: (2.0, 17.0, 40.0),
}


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


def located_photo(owner, name="located.jpg", **jpeg):
    file_obj = upload(owner, name, jpeg_bytes(size=(80, 40), gps=GPS, **jpeg))
    MediaItem.objects.update_or_create(
        file=file_obj,
        defaults={"taken_at": _at(14), "analyzed_at": _at(14), "latitude": 48.8},
    )
    return File.objects.select_related("media_item").get(pk=file_obj.pk)


def body(response):
    if response.streaming:
        return b"".join(response.streaming_content)
    return response.content


class RenditionTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.photo = located_photo(self.owner, orientation=6)
        self.album = create_album(self.owner, "Trip", files=[self.photo])

    def tearDown(self):
        cache.clear()


class PhotoRenditionTests(RenditionTestCase):
    def test_a_photo_loses_its_metadata_and_keeps_its_orientation(self):
        path, content_type = rendition(self.photo)

        self.assertEqual(content_type, "image/webp")
        with default_storage.open(path, "rb") as handle:
            img = Image.open(io.BytesIO(handle.read()))
            self.assertEqual(img.format, "WEBP")
            # 80x40 turned upright by its EXIF orientation.
            self.assertEqual(img.size, (40, 80))
            self.assertEqual(len(img.getexif()), 0)
            self.assertNotIn("exif", img.info)

    def test_a_large_photo_is_scaled_down(self):
        big = upload(
            self.owner, "big.jpg", jpeg_bytes(size=(PREVIEW_MAX_SIDE * 2, 100))
        )
        MediaItem.objects.create(file=big, analyzed_at=_at(14))
        big = File.objects.select_related("media_item").get(pk=big.pk)

        path, _ = rendition(big)

        with default_storage.open(path, "rb") as handle:
            self.assertEqual(Image.open(handle).size[0], PREVIEW_MAX_SIDE)

    def test_made_once_per_version_and_replaced_by_the_next(self):
        first, _ = rendition(self.photo)
        self.assertEqual(rendition(self.photo)[0], first)

        File.objects.filter(pk=self.photo.pk).update(content_hash="f" * 64)
        self.photo.refresh_from_db()
        second, _ = rendition(self.photo)

        self.assertNotEqual(second, first)
        self.assertFalse(default_storage.exists(first))
        self.assertTrue(default_storage.exists(second))

    def test_an_undecodable_photo_has_none(self):
        broken = upload(self.owner, "broken.jpg", b"not a jpeg")
        MediaItem.objects.create(file=broken, analyzed_at=_at(14))
        broken = File.objects.select_related("media_item").get(pk=broken.pk)

        with self.assertRaises(RenditionUnavailable):
            rendition(broken)

    def test_leaving_the_library_drops_the_renditions(self):
        path, _ = rendition(self.photo)

        with self.captureOnCommitCallbacks(execute=True):
            MediaItem.objects.filter(file=self.photo).delete()

        self.assertFalse(default_storage.exists(path))
        drop_renditions(self.photo.uuid)  # Nothing left: no error either.


@unittest.skipUnless(ffmpeg.FFMPEG and ffmpeg.FFPROBE, "ffmpeg is not installed")
class VideoRenditionTests(RenditionTestCase):
    def test_a_video_loses_its_location_and_its_tags(self):
        video = make_video(self.owner, "trip.mov", _at(15), clip="clip_iphone.mov")
        video = File.objects.select_related("media_item").get(pk=video.pk)

        path, content_type = rendition(video)

        self.assertEqual(content_type, video.mime_type)
        with (
            default_storage.open(path, "rb") as handle,
            tempfile.TemporaryDirectory() as work,
        ):
            local = os.path.join(work, "out.mov")
            with open(local, "wb") as out:
                out.write(handle.read())
            report = json.loads(
                subprocess.run(
                    [
                        ffmpeg.FFPROBE,
                        "-v",
                        "error",
                        "-print_format",
                        "json",
                        "-show_format",
                        "-show_streams",
                        local,
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
            )
        tags = json.dumps(report["format"].get("tags", {})).lower()
        self.assertNotIn("location", tags)
        self.assertNotIn("apple", tags)
        self.assertTrue(any(s["codec_type"] == "video" for s in report["streams"]))
        self.assertFalse(any(s["codec_type"] == "data" for s in report["streams"]))

    def test_without_ffmpeg_a_video_has_none(self):
        video = make_video(self.owner, "clip.webm", _at(15))
        video = File.objects.select_related("media_item").get(pk=video.pk)

        with patch("workspace.files.services.ffmpeg.FFMPEG", None):
            with self.assertRaises(RenditionUnavailable):
                rendition(video)


class ServingTests(RenditionTestCase):
    def content(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response, body(response)

    def test_a_link_without_downloads_serves_the_rendition(self):
        link = create_link(self.album, acting_user=self.owner)

        response, data = self.content(
            f"/api/v1/photos/shared/{link.token}/files/{self.photo.uuid}/content"
        )

        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertIn("located.webp", response["Content-Disposition"])
        self.assertEqual(len(Image.open(io.BytesIO(data)).getexif()), 0)

    def test_a_link_with_downloads_serves_the_original(self):
        link = create_link(self.album, acting_user=self.owner, allow_download=True)

        _, data = self.content(
            f"/api/v1/photos/shared/{link.token}/files/{self.photo.uuid}/content"
        )

        with self.photo.content.open("rb") as original:
            self.assertEqual(data, original.read())

    def test_a_viewer_without_downloads_gets_the_rendition_a_contributor_the_original(
        self,
    ):
        self.album.allow_download = False
        self.album.save(update_fields=["allow_download"])
        share_album(
            self.album,
            role=AlbumShare.Role.VIEWER,
            acting_user=self.owner,
            user=self.bob,
        )
        url = f"/api/v1/photos/albums/{self.album.uuid}/files/{self.photo.uuid}/content"
        self.client.force_login(self.bob)

        response, _ = self.content(url)
        self.assertEqual(response["Content-Type"], "image/webp")

        share_album(
            self.album,
            role=AlbumShare.Role.CONTRIBUTOR,
            acting_user=self.owner,
            user=self.bob,
        )
        response, _ = self.content(url)
        self.assertEqual(response["Content-Type"], "image/jpeg")

    def test_no_rendition_no_bytes(self):
        broken = upload(self.owner, "broken.jpg", b"not a jpeg")
        MediaItem.objects.create(file=broken, analyzed_at=_at(14))
        add_items(self.album, [broken], added_by=self.owner)
        link = create_link(self.album, acting_user=self.owner)

        response = self.client.get(
            f"/api/v1/photos/shared/{link.token}/files/{broken.uuid}/content"
        )

        self.assertEqual(response.status_code, 404)

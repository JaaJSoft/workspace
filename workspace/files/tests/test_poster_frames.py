"""Poster frames: the thumbnail pipeline on video files."""

import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from workspace.files.models import File, FileScan, ThumbnailFailure
from workspace.files.services import FileService, ffmpeg
from workspace.files.services.thumbnails import poster
from workspace.files.services.thumbnails.generation import (
    can_generate_thumbnail,
    generate_missing_thumbnails,
    generate_thumbnail,
    get_thumbnail_path,
)

from .videos import clip_bytes, requires_ffmpeg

User = get_user_model()


def _png(size=(32, 18)):
    buf = io.BytesIO()
    Image.new("RGB", size, (20, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


class PosterOffsetTests(SimpleTestCase):
    def test_a_second_in(self):
        self.assertEqual(poster.poster_offset(60.0), 1.0)
        self.assertEqual(poster.poster_offset(10.0), 1.0)

    def test_a_tenth_of_a_short_clip(self):
        self.assertAlmostEqual(poster.poster_offset(3.0), 0.3)

    def test_unknown_duration(self):
        self.assertEqual(poster.poster_offset(None), 1.0)


class VideoThumbnailTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _video(self, name="clip.webm", data=None, *, viewer=""):
        f = FileService.create_file(
            owner=self.user,
            name=name,
            content=ContentFile(clip_bytes(name) if data is None else data),
        )
        if viewer:
            File.objects.filter(pk=f.pk).update(viewer=viewer)
            f.refresh_from_db()
        self.addCleanup(default_storage.delete, get_thumbnail_path(f.uuid))
        return f


class PosterFrameTests(VideoThumbnailTestCase):
    def test_falls_back_to_the_first_frame(self):
        f = self._video()

        with (
            patch.object(ffmpeg, "probe", return_value={"format": {"duration": "30"}}),
            patch.object(ffmpeg, "extract_frame", side_effect=[b"", _png()]) as grab,
        ):
            frame = poster.poster_frame(f, (64, 64))

        self.assertEqual(frame.size, (32, 18))
        self.assertEqual([c.kwargs["at"] for c in grab.call_args_list], [1.0, 0])

    def test_a_video_with_no_frame_at_all(self):
        f = self._video()

        with (
            patch.object(ffmpeg, "probe", side_effect=ffmpeg.MediaToolError("x")),
            patch.object(ffmpeg, "extract_frame", return_value=b""),
            self.assertRaises(ffmpeg.MediaToolError),
        ):
            poster.poster_frame(f, (64, 64))


class GenerationTests(VideoThumbnailTestCase):
    @patch("workspace.files.services.thumbnails.generation.poster_frame")
    def test_a_video_gets_its_poster_frame_as_thumbnail(self, frame):
        frame.return_value = Image.new("RGB", (640, 360), (200, 0, 0))
        f = self._video()

        self.assertTrue(generate_thumbnail(f))

        with default_storage.open(get_thumbnail_path(f.uuid)) as thumb:
            with Image.open(thumb) as img:
                self.assertEqual(img.format, "WEBP")
                self.assertEqual(img.size, (512, 288))

    @patch("workspace.files.services.thumbnails.generation.poster_frame")
    def test_a_poster_failure_counts_as_an_attempt(self, frame):
        frame.side_effect = ffmpeg.MediaToolError("corrupt")
        f = self._video()

        with self.assertLogs("workspace.files.services.thumbnails.generation"):
            self.assertFalse(generate_thumbnail(f))

        self.assertEqual(ThumbnailFailure.objects.get(file=f).attempts, 1)

    @patch("workspace.files.services.ffmpeg.FFMPEG", None)
    def test_without_ffmpeg_videos_are_left_alone(self):
        f = self._video()

        self.assertFalse(can_generate_thumbnail("mp4"))
        self.assertTrue(can_generate_thumbnail("jpeg"))
        self.assertFalse(generate_thumbnail(f))
        self.assertEqual(generate_missing_thumbnails()["total"], 0)
        self.assertFalse(ThumbnailFailure.objects.exists())

    @patch("workspace.files.services.thumbnails.generation.poster_frame")
    def test_an_audio_recording_in_a_video_container_is_skipped(self, frame):
        f = self._video(viewer="audio")

        self.assertFalse(generate_thumbnail(f))
        self.assertEqual(generate_missing_thumbnails()["total"], 0)
        frame.assert_not_called()

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    @patch("workspace.files.services.thumbnails.generation.poster_frame")
    def test_a_quarantined_file_is_never_read(self, frame):
        f = self._video()
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertFalse(generate_thumbnail(f))
        frame.assert_not_called()

    @patch("workspace.files.services.thumbnails.generation.poster_frame")
    def test_the_backfill_takes_videos(self, frame):
        frame.return_value = Image.new("RGB", (64, 36))
        f = self._video()

        stats = generate_missing_thumbnails()

        self.assertEqual(stats["generated"], 1)
        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)


@requires_ffmpeg
class RealPosterFrameTests(VideoThumbnailTestCase):
    def test_portrait_video_gets_a_portrait_thumbnail(self):
        f = self._video("clip_iphone.mov")

        self.assertTrue(generate_thumbnail(f))

        with default_storage.open(get_thumbnail_path(f.uuid)) as thumb:
            with Image.open(thumb) as img:
                self.assertEqual(img.size, (54, 96))

    def test_a_file_that_only_claims_to_be_a_video(self):
        f = self._video("broken.mp4", b"\x00\x00\x00\x18ftypmp42 truncated")
        File.objects.filter(pk=f.pk).update(type="mp4")
        f.refresh_from_db()

        with self.assertLogs("workspace.files.services.thumbnails.generation"):
            self.assertFalse(generate_thumbnail(f))
        self.assertEqual(ThumbnailFailure.objects.get(file=f).attempts, 1)

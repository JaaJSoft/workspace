from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File, FileScan
from workspace.files.services import FileService
from workspace.files.tests.videos import clip_bytes, requires_ffmpeg
from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import (
    analyze_media,
    analyze_pending,
    is_media_candidate,
    pending_media_qs,
)
from workspace.photos.services.exif import PhotoMetadata
from workspace.users.services.settings import set_setting

from .images import jpeg_bytes, png_bytes, upload

User = get_user_model()


class AnalyzePhotoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def test_writes_capture_date_dimensions_and_camera(self):
        f = upload(
            self.user,
            "beach.jpg",
            jpeg_bytes(
                size=(64, 48),
                taken="2024:07:14 18:32:05",
                offset="+02:00",
                orientation=6,
                make="Canon",
                model="EOS R6",
            ),
        )

        photo = analyze_media(f)

        self.assertEqual(photo.file_id, f.pk)
        self.assertEqual(photo.taken_at, datetime(2024, 7, 14, 16, 32, 5, tzinfo=UTC))
        self.assertEqual((photo.width, photo.height), (48, 64))
        self.assertEqual((photo.camera_make, photo.camera_model), ("Canon", "EOS R6"))
        self.assertEqual(photo.content_hash, f.content_hash)
        self.assertIsNotNone(photo.analyzed_at)

    def test_no_capture_date_is_null_never_the_upload_date(self):
        f = upload(self.user, "screenshot.png", png_bytes())

        photo = analyze_media(f)

        self.assertIsNone(photo.taken_at)
        self.assertEqual((photo.width, photo.height), (32, 32))

    def test_wall_clock_capture_time_is_read_in_the_owners_timezone(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        f = upload(self.user, "dinner.jpg", jpeg_bytes(taken="2024:07:14 23:30:00"))

        photo = analyze_media(f)

        # 23:30 in Paris (UTC+2 in July) is 21:30 UTC, and still the 14th.
        self.assertEqual(photo.taken_at, datetime(2024, 7, 14, 21, 30, tzinfo=UTC))

    def test_second_analysis_updates_the_same_row(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        first = analyze_media(f)

        second = analyze_media(f)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MediaItem.objects.filter(file=f).count(), 1)

    def test_non_image_is_left_alone(self):
        f = upload(self.user, "notes.txt", b"hello world")

        self.assertIsNone(analyze_media(f))
        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_svg_is_not_a_photo(self):
        f = upload(
            self.user,
            "logo.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"></svg>',
        )

        self.assertIsNone(analyze_media(f))

    def test_undecodable_image_gets_an_empty_row(self):
        f = upload(self.user, "broken.jpg", b"\xff\xd8\xff not really a jpeg")
        File.objects.filter(pk=f.pk).update(type="jpeg")
        f.refresh_from_db()

        with self.assertLogs("workspace.photos.services.analysis", "INFO"):
            photo = analyze_media(f)

        self.assertIsNotNone(photo)
        self.assertIsNone(photo.taken_at)
        self.assertIsNone(photo.width)
        self.assertIsNone(photo.height)

    def test_missing_blob_writes_nothing(self):
        f = upload(self.user, "gone.jpg")
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.photos.services.analysis", "WARNING"):
            self.assertIsNone(analyze_media(f))
        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_content_replaced_while_reading_writes_nothing(self):
        f = upload(self.user, "race.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))

        def replaced_meanwhile(stream, **kwargs):
            File.objects.filter(pk=f.pk).update(content_hash="f" * 64)
            return PhotoMetadata()

        with patch(
            "workspace.photos.services.analysis.read_metadata",
            side_effect=replaced_meanwhile,
        ):
            self.assertIsNone(analyze_media(f))
        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_hard_deleted_while_reading_writes_nothing(self):
        f = upload(self.user, "vanished.jpg")

        def deleted_meanwhile(stream, **kwargs):
            File.objects.filter(pk=f.pk).delete()
            return PhotoMetadata()

        with patch(
            "workspace.photos.services.analysis.read_metadata",
            side_effect=deleted_meanwhile,
        ):
            self.assertIsNone(analyze_media(f))
        self.assertFalse(MediaItem.objects.exists())


class PendingPhotosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="p")

    def _pending(self, **kwargs):
        return set(pending_media_qs(**kwargs).values_list("pk", flat=True))

    def test_image_without_a_row_is_pending(self):
        f = upload(self.user, "a.jpg")

        self.assertEqual(self._pending(), {f.pk})

    def test_analyzed_image_is_not(self):
        f = upload(self.user, "a.jpg")
        analyze_media(f)

        self.assertEqual(self._pending(), set())

    def test_row_describing_replaced_bytes_is_pending(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        analyze_media(f)

        FileService.update_content(
            f, ContentFile(jpeg_bytes(taken="2025:01:01 10:00:00"), name="a.jpg")
        )

        self.assertEqual(self._pending(), {f.pk})

    def test_file_without_a_hash_is_pending_only_without_a_row(self):
        f = upload(self.user, "legacy.jpg")
        File.objects.filter(pk=f.pk).update(content_hash="")
        f.refresh_from_db()
        self.assertEqual(self._pending(), {f.pk})

        analyze_media(f)

        self.assertEqual(self._pending(), set())

    def test_trashed_image_is_not_pending(self):
        f = upload(self.user, "a.jpg")
        FileService.soft_delete(f, acting_user=self.user)

        self.assertEqual(self._pending(), set())

    def test_other_files_are_not_pending(self):
        upload(self.user, "a.txt", b"text")
        FileService.create_folder(owner=self.user, name="Pictures")

        self.assertEqual(self._pending(), set())

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_quarantined_image_is_not_pending(self):
        f = upload(self.user, "a.jpg")
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertEqual(self._pending(), set())

    def test_reanalyze_takes_every_image(self):
        f = upload(self.user, "a.jpg")
        analyze_media(f)

        self.assertEqual(self._pending(reanalyze=True), {f.pk})


class AnalyzeVideoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dave", password="p")

    def tearDown(self):
        cache.clear()

    def _video(self, name="clip_iphone.mov", data=None):
        return upload(self.user, name, clip_bytes(name) if data is None else data)

    @requires_ffmpeg
    def test_writes_recording_date_size_duration_codec_and_place(self):
        f = self._video()

        item = analyze_media(f)

        self.assertEqual(item.media_type, MediaItem.MediaType.VIDEO)
        self.assertEqual(item.taken_at, datetime(2024, 7, 14, 10, 30, 1, tzinfo=UTC))
        self.assertEqual((item.width, item.height), (54, 96))
        self.assertEqual((item.duration, item.codec), (3.0, "h264"))
        self.assertEqual((item.latitude, item.longitude), (48.8584, 2.2945))
        self.assertEqual((item.camera_make, item.camera_model), ("Apple", "iPhone 15"))
        self.assertEqual(item.content_hash, f.content_hash)

    @requires_ffmpeg
    def test_a_file_ffprobe_cannot_read_gets_an_empty_row(self):
        f = self._video("broken.mp4", b"\x00\x00\x00\x18ftypmp42 truncated")
        File.objects.filter(pk=f.pk).update(type="mp4")
        f.refresh_from_db()

        with self.assertLogs("workspace.photos.services.analysis", "INFO"):
            item = analyze_media(f)

        self.assertEqual(item.media_type, MediaItem.MediaType.VIDEO)
        self.assertIsNone(item.taken_at)
        self.assertIsNone(item.duration)

    @patch("workspace.files.services.ffmpeg.FFPROBE", None)
    def test_without_ffprobe_the_video_goes_to_undated(self):
        f = self._video()

        with self.assertLogs("workspace.photos.services.analysis", "INFO"):
            item = analyze_media(f)

        self.assertEqual(item.media_type, MediaItem.MediaType.VIDEO)
        self.assertIsNone(item.taken_at)
        self.assertEqual(self._pending(), set())

    def test_missing_blob_writes_nothing(self):
        f = self._video()
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.photos.services.analysis", "WARNING"):
            self.assertIsNone(analyze_media(f))
        self.assertFalse(MediaItem.objects.exists())

    def test_a_photo_replaced_by_a_video_becomes_a_video(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        analyze_media(f)
        FileService.update_content(
            f, ContentFile(clip_bytes("clip.webm"), name="a.jpg")
        )
        f.refresh_from_db()

        with patch("workspace.files.services.ffmpeg.FFPROBE", None):
            with self.assertLogs("workspace.photos.services.analysis", "INFO"):
                item = analyze_media(f)

        self.assertEqual(item.media_type, MediaItem.MediaType.VIDEO)
        self.assertIsNone(item.taken_at)
        self.assertIsNone(item.width)

    def test_videos_are_pending_until_analyzed(self):
        f = self._video()

        self.assertEqual(self._pending(), {f.pk})

    def test_an_audio_recording_in_a_video_container_is_not_a_video(self):
        f = self._video("clip.webm")
        File.objects.filter(pk=f.pk).update(viewer="audio")
        f.refresh_from_db()

        self.assertFalse(is_media_candidate(f))
        self.assertIsNone(analyze_media(f))
        self.assertEqual(self._pending(), set())

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_file_is_never_read(self):
        f = self._video()
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        with patch("workspace.photos.services.video.read_metadata") as read:
            self.assertIsNone(analyze_media(f))
        read.assert_not_called()

    def _pending(self):
        return set(pending_media_qs().values_list("pk", flat=True))


class AnalyzePendingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="carol", password="p")

    def test_fills_the_library_and_is_idempotent(self):
        dated = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        undated = upload(self.user, "b.png", png_bytes())

        self.assertEqual(analyze_pending(), {"analyzed": 2, "skipped": 0})
        self.assertEqual(
            set(MediaItem.objects.values_list("file_id", flat=True)),
            {dated.pk, undated.pk},
        )
        self.assertEqual(analyze_pending(), {"analyzed": 0, "skipped": 0})

    def test_limit(self):
        upload(self.user, "a.jpg")
        upload(self.user, "b.jpg")

        self.assertEqual(analyze_pending(limit=1), {"analyzed": 1, "skipped": 0})
        self.assertEqual(MediaItem.objects.count(), 1)

    def test_an_unreadable_blob_does_not_stop_the_pass(self):
        broken = upload(self.user, "a.jpg")
        broken.content.storage.delete(broken.content.name)
        fine = upload(self.user, "b.jpg")

        with self.assertLogs("workspace.photos.services.analysis", "WARNING"):
            stats = analyze_pending()

        self.assertEqual(stats, {"analyzed": 1, "skipped": 1})
        self.assertEqual(
            list(MediaItem.objects.values_list("file_id", flat=True)), [fine.pk]
        )

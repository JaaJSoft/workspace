from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File, FileScan
from workspace.files.services import FileService
from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import (
    analyze_pending,
    analyze_photo,
    pending_photos_qs,
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

        photo = analyze_photo(f)

        self.assertEqual(photo.file_id, f.pk)
        self.assertEqual(photo.taken_at, datetime(2024, 7, 14, 16, 32, 5, tzinfo=UTC))
        self.assertEqual((photo.width, photo.height), (48, 64))
        self.assertEqual((photo.camera_make, photo.camera_model), ("Canon", "EOS R6"))
        self.assertEqual(photo.content_hash, f.content_hash)
        self.assertIsNotNone(photo.analyzed_at)

    def test_no_capture_date_is_null_never_the_upload_date(self):
        f = upload(self.user, "screenshot.png", png_bytes())

        photo = analyze_photo(f)

        self.assertIsNone(photo.taken_at)
        self.assertEqual((photo.width, photo.height), (32, 32))

    def test_wall_clock_capture_time_is_read_in_the_owners_timezone(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        f = upload(self.user, "dinner.jpg", jpeg_bytes(taken="2024:07:14 23:30:00"))

        photo = analyze_photo(f)

        # 23:30 in Paris (UTC+2 in July) is 21:30 UTC, and still the 14th.
        self.assertEqual(photo.taken_at, datetime(2024, 7, 14, 21, 30, tzinfo=UTC))

    def test_second_analysis_updates_the_same_row(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        first = analyze_photo(f)

        second = analyze_photo(f)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MediaItem.objects.filter(file=f).count(), 1)

    def test_non_image_is_left_alone(self):
        f = upload(self.user, "notes.txt", b"hello world")

        self.assertIsNone(analyze_photo(f))
        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_svg_is_not_a_photo(self):
        f = upload(
            self.user,
            "logo.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"></svg>',
        )

        self.assertIsNone(analyze_photo(f))

    def test_undecodable_image_gets_an_empty_row(self):
        f = upload(self.user, "broken.jpg", b"\xff\xd8\xff not really a jpeg")
        File.objects.filter(pk=f.pk).update(type="jpeg")
        f.refresh_from_db()

        with self.assertLogs("workspace.photos.services.analysis", "INFO"):
            photo = analyze_photo(f)

        self.assertIsNotNone(photo)
        self.assertIsNone(photo.taken_at)
        self.assertIsNone(photo.width)
        self.assertIsNone(photo.height)

    def test_missing_blob_writes_nothing(self):
        f = upload(self.user, "gone.jpg")
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.photos.services.analysis", "WARNING"):
            self.assertIsNone(analyze_photo(f))
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
            self.assertIsNone(analyze_photo(f))
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
            self.assertIsNone(analyze_photo(f))
        self.assertFalse(MediaItem.objects.exists())


class PendingPhotosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="p")

    def _pending(self, **kwargs):
        return set(pending_photos_qs(**kwargs).values_list("pk", flat=True))

    def test_image_without_a_row_is_pending(self):
        f = upload(self.user, "a.jpg")

        self.assertEqual(self._pending(), {f.pk})

    def test_analyzed_image_is_not(self):
        f = upload(self.user, "a.jpg")
        analyze_photo(f)

        self.assertEqual(self._pending(), set())

    def test_row_describing_replaced_bytes_is_pending(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        analyze_photo(f)

        FileService.update_content(
            f, ContentFile(jpeg_bytes(taken="2025:01:01 10:00:00"), name="a.jpg")
        )

        self.assertEqual(self._pending(), {f.pk})

    def test_file_without_a_hash_is_pending_only_without_a_row(self):
        f = upload(self.user, "legacy.jpg")
        File.objects.filter(pk=f.pk).update(content_hash="")
        f.refresh_from_db()
        self.assertEqual(self._pending(), {f.pk})

        analyze_photo(f)

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
        analyze_photo(f)

        self.assertEqual(self._pending(reanalyze=True), {f.pk})


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

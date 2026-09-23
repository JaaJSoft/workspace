from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from workspace.photos.models import Photo
from workspace.photos.tasks import analyze_pending, analyze_photo

from .images import jpeg_bytes, upload

User = get_user_model()


class AnalyzePendingTaskTests(TestCase):
    def test_catches_up_on_unanalyzed_images(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))

        self.assertEqual(analyze_pending.apply().get(), {"analyzed": 1, "skipped": 0})
        self.assertTrue(Photo.objects.filter(file=f).exists())


class AnalyzePhotoTaskTests(TestCase):
    def test_analyzes_the_file(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.jpg")

        self.assertEqual(analyze_photo.apply(args=[str(f.pk)]).get(), {"status": "ok"})
        self.assertTrue(Photo.objects.filter(file=f).exists())

    def test_a_non_photo_is_skipped(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.txt", b"text")

        self.assertEqual(
            analyze_photo.apply(args=[str(f.pk)]).get(), {"status": "skipped"}
        )

    def test_unknown_or_malformed_id(self):
        for file_uuid in ("00000000-0000-0000-0000-000000000000", "nope", None):
            with self.subTest(file_uuid=file_uuid):
                self.assertEqual(
                    analyze_photo.apply(args=[file_uuid]).get(),
                    {"status": "not_found"},
                )


class BeatScheduleTests(SimpleTestCase):
    def test_catch_up_runs_hourly(self):
        entry = settings.CELERY_BEAT_SCHEDULE["analyze-photos"]

        self.assertEqual(entry["task"], "photos.analyze_pending")
        self.assertEqual(entry["schedule"], 3600.0)

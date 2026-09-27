"""The data migration that forgets the video readings of retyped photos."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File
from workspace.files.tests.rasters import heic_bytes
from workspace.photos.models import FaceAnalysis, MediaItem

from .images import upload

migration = importlib.import_module(
    "workspace.photos.migrations.0011_reread_retyped_images"
)

User = get_user_model()


class ForgetVideoReadingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reread", password="x")

    def _read(self, file_obj, media_type):
        MediaItem.objects.update_or_create(
            file=file_obj,
            defaults={"media_type": media_type, "analyzed_at": timezone.now()},
        )
        FaceAnalysis.objects.update_or_create(
            file=file_obj,
            defaults={
                "owner": self.user,
                "backend": "fake",
                "version": 1,
                "analyzed_at": timezone.now(),
            },
        )

    def test_rows_of_a_retyped_heic_are_dropped(self):
        heic = upload(self.user, "IMG_0001.HEIC", heic_bytes())
        self.assertEqual(File.objects.get(pk=heic.pk).type, "heif")
        self._read(heic, MediaItem.MediaType.VIDEO)

        migration.forget_video_readings(apps, schema_editor_stub())

        self.assertFalse(MediaItem.objects.filter(file=heic).exists())
        self.assertFalse(FaceAnalysis.objects.filter(file=heic).exists())

    def test_rows_of_other_photos_are_kept(self):
        jpeg = upload(self.user, "a.jpg")
        self._read(jpeg, MediaItem.MediaType.PHOTO)

        migration.forget_video_readings(apps, schema_editor_stub())

        self.assertTrue(MediaItem.objects.filter(file=jpeg).exists())
        self.assertTrue(FaceAnalysis.objects.filter(file=jpeg).exists())

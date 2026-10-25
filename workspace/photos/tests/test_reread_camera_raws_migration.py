"""The data migration that forgets what was read off RAW photos as TIFFs."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File
from workspace.files.tests.rasters import tiff_raw_bytes
from workspace.photos.models import FaceAnalysis, MediaItem

from .images import upload

migration = importlib.import_module(
    "workspace.photos.migrations.0026_reread_camera_raws"
)

User = get_user_model()


class ForgetThumbnailReadingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rereadraw", password="x")

    def _read(self, file_obj):
        MediaItem.objects.update_or_create(
            file=file_obj,
            defaults={
                "media_type": MediaItem.MediaType.PHOTO,
                "width": 160,
                "height": 120,
                "analyzed_at": timezone.now(),
            },
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

    def test_rows_of_a_raw_are_dropped(self):
        nef = upload(self.user, "DSC_0001.NEF", tiff_raw_bytes())
        self.assertEqual(File.objects.get(pk=nef.pk).type, "nef")
        self._read(nef)

        migration.forget_thumbnail_readings(apps, schema_editor_stub())

        self.assertFalse(MediaItem.objects.filter(file=nef).exists())
        self.assertFalse(FaceAnalysis.objects.filter(file=nef).exists())

    def test_rows_of_other_photos_are_kept(self):
        jpeg = upload(self.user, "a.jpg")
        self._read(jpeg)

        migration.forget_thumbnail_readings(apps, schema_editor_stub())

        self.assertTrue(MediaItem.objects.filter(file=jpeg).exists())
        self.assertTrue(FaceAnalysis.objects.filter(file=jpeg).exists())

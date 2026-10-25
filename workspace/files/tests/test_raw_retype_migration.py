"""The data migration that retypes camera RAW photos detected as TIFF."""

import importlib
import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File, ProcessingFailure
from workspace.files.services import FileService
from workspace.files.tests.rasters import dng_bytes

migration = importlib.import_module(
    "workspace.files.migrations.0074_retype_camera_raws"
)

User = get_user_model()


class RetypeCameraRawsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="retyperaw", password="x")

    def _stored_as(self, name, data, file_type="tiff"):
        """A file as the old detection left it, with its thumbnail drawn."""
        file_obj = FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )
        File.objects.filter(pk=file_obj.pk).update(
            type=file_type, category="image", mime_type="image/tiff", has_thumbnail=True
        )
        return file_obj

    def _migrate(self):
        migration.retype_raws(apps, schema_editor_stub())

    def test_a_dng_stored_as_a_tiff_becomes_a_dng(self):
        dng = self._stored_as("IMG_0001.DNG", dng_bytes())

        self._migrate()

        self.assertEqual(
            File.objects.filter(pk=dng.pk)
            .values_list("type", "category", "mime_type", "has_thumbnail")
            .get(),
            ("dng", "image", "image/x-adobe-dng", False),
        )

    def test_an_unknown_raw_is_retyped_too(self):
        dng = self._stored_as("IMG_0002.dng", dng_bytes(), file_type="unknown")

        self._migrate()

        self.assertEqual(File.objects.get(pk=dng.pk).type, "dng")

    def test_a_plain_tiff_named_nef_stays_a_tiff(self):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), "red").save(buffer, format="TIFF")
        tiff = self._stored_as("scan.nef", buffer.getvalue())

        self._migrate()

        self.assertEqual(File.objects.get(pk=tiff.pk).type, "tiff")

    def test_processing_failures_are_dropped(self):
        dng = self._stored_as("IMG_0003.DNG", dng_bytes())
        ProcessingFailure.objects.create(
            file=dng,
            processor="thumbnails",
            attempts=3,
            last_attempt_at=timezone.now(),
        )

        self._migrate()

        self.assertFalse(ProcessingFailure.objects.filter(file=dng).exists())

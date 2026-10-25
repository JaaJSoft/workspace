"""The data migration that retypes camera RAW photos detected as TIFF."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File, ProcessingFailure
from workspace.files.services import FileService
from workspace.files.tests.rasters import cr3_bytes, tiff_raw_bytes

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

    def test_a_nef_stored_as_a_tiff_becomes_a_nef(self):
        nef = self._stored_as("DSC_0001.NEF", tiff_raw_bytes())

        self._migrate()

        self.assertEqual(
            File.objects.filter(pk=nef.pk)
            .values_list("type", "category", "mime_type", "has_thumbnail")
            .get(),
            ("nef", "image", "image/x-nikon-nef", False),
        )

    def test_an_unknown_cr3_becomes_a_cr3(self):
        cr3 = self._stored_as("IMG_0001.CR3", cr3_bytes(), file_type="unknown")

        self._migrate()

        self.assertEqual(File.objects.get(pk=cr3.pk).type, "cr3")

    def test_a_plain_tiff_named_nef_stays_a_tiff(self):
        tiff = self._stored_as("scan.nef", tiff_raw_bytes(sub_ifds=False))

        self._migrate()

        self.assertEqual(File.objects.get(pk=tiff.pk).type, "tiff")

    def test_processing_failures_are_dropped(self):
        nef = self._stored_as("DSC_0002.nef", tiff_raw_bytes())
        ProcessingFailure.objects.create(
            file=nef,
            processor="thumbnails",
            attempts=3,
            last_attempt_at=timezone.now(),
        )

        self._migrate()

        self.assertFalse(ProcessingFailure.objects.filter(file=nef).exists())

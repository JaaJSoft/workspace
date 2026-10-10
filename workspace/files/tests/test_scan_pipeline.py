"""The malware scan as the first step of the upload pipeline."""

import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from workspace.files.models import File, FileScan
from workspace.files.services import FileService
from workspace.files.services.processors import get_processor, run_pipeline
from workspace.files.services.scanning.base import ScanVerdict

User = get_user_model()
ENABLED = {"FILES_MALWARE_SCAN_ENABLED": True}


class _StubScanner:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    def scan(self, stream, *, name=""):
        self.calls += 1
        stream.read()
        return ScanVerdict(status=self.status, signature="Eicar-Test-Signature")

    def health(self):
        raise AssertionError("not used here")


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class ScanPipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="evt", password="p")

    def _upload(self, name="a.txt", body=b"x"):
        return FileService.create_file(
            owner=self.user, name=name, content=ContentFile(body, name=name)
        )

    def _run(self, file_obj, status=FileScan.Status.CLEAN, **settings):
        scanner = _StubScanner(status)
        with (
            override_settings(**{**ENABLED, **settings}),
            patch(
                "workspace.files.services.scanning.registry.get_scanner",
                return_value=scanner,
            ),
        ):
            run_pipeline(file_obj.uuid)
        return scanner

    def test_the_scan_runs_before_every_other_processor(self):
        from workspace.files.services.processors import registered_processors

        self.assertEqual(registered_processors()[0], get_processor("malware_scan"))

    def test_an_upload_is_scanned(self):
        f = self._upload()

        scanner = self._run(f)

        self.assertEqual(scanner.calls, 1)
        self.assertEqual(FileScan.objects.get(file=f).status, FileScan.Status.CLEAN)

    def test_disabled_scanning_scans_nothing(self):
        f = self._upload()

        scanner = self._run(f, FILES_MALWARE_SCAN_ENABLED=False)

        self.assertEqual(scanner.calls, 0)
        self.assertFalse(FileScan.objects.exists())

    def test_a_trashed_file_is_not_scanned(self):
        f = self._upload()
        FileService.soft_delete(f, acting_user=self.user)

        scanner = self._run(f)

        self.assertEqual(scanner.calls, 0)

    def test_an_infected_image_is_never_thumbnailed(self):
        """The processors after the scan see its verdict before reading the bytes."""
        f = self._upload("pic.png", _png_bytes())

        self._run(f, FileScan.Status.INFECTED)

        f.refresh_from_db()
        self.assertEqual(FileScan.objects.get(file=f).status, FileScan.Status.INFECTED)
        self.assertFalse(f.has_thumbnail)
        with override_settings(**ENABLED):
            self.assertEqual(f.effective_processing_status(), "quarantined")

    def test_a_clean_image_is_thumbnailed_after_its_scan(self):
        f = self._upload("pic.png", _png_bytes())

        self._run(f)

        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)
        self.assertEqual(f.processing_status, File.ProcessingStatus.READY)

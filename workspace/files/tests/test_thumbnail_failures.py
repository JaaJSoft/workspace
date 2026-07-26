"""Tests for thumbnail failure parking.

A file that can never produce a thumbnail must stop being re-decoded by the
hourly backfill after a bounded number of attempts, while a file that fails
transiently - or gets repaired - must still be picked up.
"""

import io
import logging

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from workspace.files.models import ThumbnailFailure
from workspace.files.services import FileService
from workspace.files.services.thumbnails import get_thumbnail_path

User = get_user_model()
logger = logging.getLogger(__name__)


def _image_bytes(size=(40, 40), fmt="JPEG"):
    img = Image.new("RGB", size, (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class ThumbnailFileMixin:
    """Shared fixture: a user plus helpers to build image and non-image files.

    NOTE: FileService.create_file records a CREATED event whose dispatch is
    wrapped in transaction.on_commit, which never runs under TestCase. No
    thumbnail attempt is consumed by file creation here.

    Kept as a plain mixin, not a TestCase subclass, so the same helpers serve
    both the service tests (Django TestCase) and the endpoint tests, which need
    DRF's APITestCase for ``format="multipart"`` and ``force_authenticate``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="thumbfail", password="p")

    def _make_file(self, name, data, ftype, mime):
        f = FileService.create_file(
            owner=self.user,
            name=name,
            content=ContentFile(data, name=name),
            mime_type=mime,
        )
        f.type = ftype
        f.save(update_fields=["type"])
        self.addCleanup(self._cleanup_thumb, f.uuid)
        return f

    def _make_broken_image(self, name="broken.jpg"):
        """A file labelled as a JPEG whose bytes Pillow cannot decode."""
        return self._make_file(name, b"definitely not an image", "jpeg", "image/jpeg")

    def _make_valid_image(self, name="ok.jpg"):
        return self._make_file(name, _image_bytes(), "jpeg", "image/jpeg")

    def _cleanup_thumb(self, uuid):
        path = get_thumbnail_path(uuid)
        try:
            if default_storage.exists(path):
                default_storage.delete(path)
        except PermissionError, OSError:
            # Best-effort: a Windows file lock must not fail the test run.
            logger.debug("could not delete test thumbnail %s", uuid)


class ThumbnailFailureTestCase(ThumbnailFileMixin, TestCase):
    """Base for the service-level tests."""


class ThumbnailFailureAPITestCase(ThumbnailFileMixin, APITestCase):
    """Base for the tests that go through a real REST endpoint."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)


class ThumbnailFailureModelTests(ThumbnailFailureTestCase):
    def test_only_one_failure_row_per_file(self):
        f = self._make_broken_image()
        ThumbnailFailure.objects.create(
            file=f, attempts=1, last_attempt_at=timezone.now()
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ThumbnailFailure.objects.create(
                file=f, attempts=1, last_attempt_at=timezone.now()
            )

    def test_deleting_the_file_removes_the_failure_row(self):
        f = self._make_broken_image()
        ThumbnailFailure.objects.create(
            file=f, attempts=2, last_attempt_at=timezone.now()
        )

        # File.delete() soft-deletes by default (an UPDATE, not a DELETE), so
        # the FK CASCADE never fires; hard=True forces a real row delete, per
        # the convention used by every other satellite-table cascade test
        # (test_links.py, test_sharing.py, test_tags.py, test_share_links.py).
        f.delete(hard=True)

        self.assertEqual(ThumbnailFailure.objects.count(), 0)

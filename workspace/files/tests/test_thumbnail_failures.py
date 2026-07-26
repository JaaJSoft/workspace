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
        cls.staff = User.objects.create_user(
            username="thumbstaff", password="p", is_staff=True
        )

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


class RecordFailureTests(ThumbnailFailureTestCase):
    def test_first_failure_creates_a_row_with_one_attempt(self):
        from workspace.files.services.thumbnail_failures import record_failure

        f = self._make_broken_image()

        attempts = record_failure(f, ValueError("boom"))

        self.assertEqual(attempts, 1)
        row = ThumbnailFailure.objects.get(file=f)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.last_error, "boom")

    def test_repeated_failures_increment_the_same_row(self):
        from workspace.files.services.thumbnail_failures import record_failure

        f = self._make_broken_image()

        record_failure(f, ValueError("first"))
        attempts = record_failure(f, ValueError("second"))

        self.assertEqual(attempts, 2)
        self.assertEqual(ThumbnailFailure.objects.filter(file=f).count(), 1)
        row = ThumbnailFailure.objects.get(file=f)
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.last_error, "second")

    def test_last_attempt_at_moves_forward_on_each_failure(self):
        # Pins the "no auto_now" decision: auto_now never fires on a queryset
        # .update(), so the timestamp would freeze at the first attempt.
        from workspace.files.services.thumbnail_failures import record_failure

        f = self._make_broken_image()
        record_failure(f, ValueError("first"))
        first_seen = ThumbnailFailure.objects.get(file=f).last_attempt_at

        record_failure(f, ValueError("second"))

        self.assertGreater(
            ThumbnailFailure.objects.get(file=f).last_attempt_at, first_seen
        )

    def test_long_error_is_truncated_to_the_column_width(self):
        from workspace.files.services.thumbnail_failures import record_failure

        f = self._make_broken_image()

        record_failure(f, ValueError("x" * 500))

        self.assertEqual(len(ThumbnailFailure.objects.get(file=f).last_error), 200)

    def test_clear_failure_removes_the_row(self):
        from workspace.files.services.thumbnail_failures import (
            clear_failure,
            record_failure,
        )

        f = self._make_broken_image()
        record_failure(f, ValueError("boom"))

        clear_failure(f)

        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())

    def test_clear_failure_is_a_noop_without_a_row(self):
        from workspace.files.services.thumbnail_failures import clear_failure

        f = self._make_valid_image()

        clear_failure(f)  # must not raise

        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())

    def test_parked_file_ids_holds_only_files_at_the_budget(self):
        from workspace.files.services.thumbnail_failures import (
            MAX_THUMBNAIL_ATTEMPTS,
            parked_file_ids,
        )

        still_trying = self._make_broken_image("a.jpg")
        parked = self._make_broken_image("b.jpg")
        ThumbnailFailure.objects.create(
            file=still_trying,
            attempts=MAX_THUMBNAIL_ATTEMPTS - 1,
            last_attempt_at=timezone.now(),
        )
        ThumbnailFailure.objects.create(
            file=parked,
            attempts=MAX_THUMBNAIL_ATTEMPTS,
            last_attempt_at=timezone.now(),
        )

        ids = [row["file_id"] for row in parked_file_ids()]

        self.assertEqual(ids, [parked.uuid])

    def test_count_parked_since_ignores_older_rows_and_unfinished_budgets(self):
        from datetime import timedelta

        from workspace.files.services.thumbnail_failures import (
            MAX_THUMBNAIL_ATTEMPTS,
            count_parked_since,
        )

        now = timezone.now()
        cutoff = now - timedelta(minutes=5)
        # At the budget but touched before the cutoff.
        ThumbnailFailure.objects.create(
            file=self._make_broken_image("old.jpg"),
            attempts=MAX_THUMBNAIL_ATTEMPTS,
            last_attempt_at=cutoff - timedelta(minutes=1),
        )
        # Touched after the cutoff but still has budget left.
        ThumbnailFailure.objects.create(
            file=self._make_broken_image("young.jpg"),
            attempts=MAX_THUMBNAIL_ATTEMPTS - 1,
            last_attempt_at=now,
        )
        # Both: parked during the window.
        ThumbnailFailure.objects.create(
            file=self._make_broken_image("parked.jpg"),
            attempts=MAX_THUMBNAIL_ATTEMPTS,
            last_attempt_at=now,
        )

        self.assertEqual(count_parked_since(cutoff), 1)

    def test_clear_all_failures_purges_every_row(self):
        from workspace.files.services.thumbnail_failures import (
            clear_all_failures,
            record_failure,
        )

        record_failure(self._make_broken_image("a.jpg"), ValueError("boom"))
        record_failure(self._make_broken_image("b.jpg"), ValueError("boom"))

        deleted = clear_all_failures()

        self.assertEqual(deleted, 2)
        self.assertEqual(ThumbnailFailure.objects.count(), 0)


class GenerateThumbnailBookkeepingTests(ThumbnailFailureTestCase):
    def test_failed_generation_records_an_attempt(self):
        from workspace.files.services.thumbnails import generate_thumbnail

        f = self._make_broken_image()

        self.assertFalse(generate_thumbnail(f))

        row = ThumbnailFailure.objects.get(file=f)
        self.assertEqual(row.attempts, 1)
        self.assertTrue(row.last_error, "the decoder error should be recorded")

    def test_successful_generation_clears_a_previous_failure(self):
        from workspace.files.services.thumbnail_failures import record_failure
        from workspace.files.services.thumbnails import generate_thumbnail

        f = self._make_valid_image()
        record_failure(f, ValueError("a previous transient failure"))

        self.assertTrue(generate_thumbnail(f))

        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())

    def test_skipped_generation_records_nothing(self):
        # A type outside THUMBNAIL_LABELS returns False without ever decoding.
        # That is not a failure and must not consume an attempt.
        from workspace.files.services.thumbnails import generate_thumbnail

        f = self._make_file("doc.pdf", b"%PDF-1.4", "pdf", "application/pdf")

        self.assertFalse(generate_thumbnail(f))

        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())


class BackfillParkingTests(ThumbnailFailureTestCase):
    def test_permanently_failing_file_is_not_retried_forever(self):
        """The regression test for issue #426.

        Against the pre-fix code, pass 4 attempts the file again and
        stats['total'] is 1 - which is the whole bug.
        """
        from workspace.files.services.thumbnail_failures import MAX_THUMBNAIL_ATTEMPTS
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        f = self._make_broken_image()

        for attempt in range(1, MAX_THUMBNAIL_ATTEMPTS + 1):
            stats = generate_missing_thumbnails()
            self.assertEqual(stats["total"], 1, f"pass {attempt} should attempt it")
            self.assertEqual(stats["failed"], 1, f"pass {attempt} should fail")

        stats = generate_missing_thumbnails()

        self.assertEqual(
            stats["total"], 0, "a parked file must never be attempted again"
        )
        self.assertEqual(
            ThumbnailFailure.objects.get(file=f).attempts, MAX_THUMBNAIL_ATTEMPTS
        )

    def test_transient_failure_is_retried_and_then_forgotten(self):
        from workspace.files.services.thumbnail_failures import record_failure
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        f = self._make_valid_image()
        record_failure(f, OSError("storage was briefly unreachable"))

        stats = generate_missing_thumbnails()

        self.assertEqual(stats["generated"], 1)
        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())
        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)

    def test_parked_counter_reports_files_that_burned_their_budget_this_pass(self):
        from workspace.files.services.thumbnail_failures import MAX_THUMBNAIL_ATTEMPTS
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        f = self._make_broken_image()
        ThumbnailFailure.objects.create(
            file=f,
            attempts=MAX_THUMBNAIL_ATTEMPTS - 1,
            last_attempt_at=timezone.now(),
        )

        stats = generate_missing_thumbnails()

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["parked"], 1)

    def test_pass_that_parks_nothing_reports_zero(self):
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        self._make_valid_image()

        stats = generate_missing_thumbnails()

        self.assertEqual(stats["generated"], 1)
        self.assertEqual(stats["parked"], 0)


class ContentReplacementResetsBudgetTests(ThumbnailFailureTestCase):
    def test_update_content_clears_the_failure_row(self):
        from workspace.files.services.thumbnail_failures import (
            MAX_THUMBNAIL_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image()
        for _ in range(MAX_THUMBNAIL_ATTEMPTS):
            record_failure(f, ValueError("boom"))

        FileService.update_content(
            f,
            ContentFile(_image_bytes(), name="broken.jpg"),
            name="broken.jpg",
            mime_type="image/jpeg",
        )

        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())

    def test_replace_content_storage_clears_the_failure_row(self):
        # The WebDAV PUT path. Pinned separately because it is a distinct
        # method, not a wrapper around update_content.
        from workspace.files.services.thumbnail_failures import record_failure

        f = self._make_broken_image()
        record_failure(f, ValueError("boom"))

        FileService.replace_content_storage(
            f, storage_path=f.content.name, size=f.size or 1
        )

        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())

    def test_parked_file_is_scanned_again_after_its_content_is_replaced(self):
        from workspace.files.services.thumbnail_failures import (
            MAX_THUMBNAIL_ATTEMPTS,
            record_failure,
        )
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        f = self._make_broken_image()
        for _ in range(MAX_THUMBNAIL_ATTEMPTS):
            record_failure(f, ValueError("boom"))
        self.assertEqual(generate_missing_thumbnails()["total"], 0)

        FileService.update_content(
            f,
            ContentFile(_image_bytes(), name="broken.jpg"),
            name="broken.jpg",
            mime_type="image/jpeg",
        )

        stats = generate_missing_thumbnails()
        self.assertEqual(stats["generated"], 1)


class ContentReplacementViaApiTests(ThumbnailFailureAPITestCase):
    """The unit tests above, backed by one pass through the real REST entry point."""

    def test_patching_a_parked_file_makes_it_eligible_again(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from rest_framework import status

        from workspace.files.services.thumbnail_failures import (
            MAX_THUMBNAIL_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image()
        for _ in range(MAX_THUMBNAIL_ATTEMPTS):
            record_failure(f, ValueError("boom"))

        resp = self.client.patch(
            f"/api/v1/files/{f.uuid}",
            {
                "content": SimpleUploadedFile(
                    "broken.jpg", _image_bytes(), content_type="image/jpeg"
                )
            },
            format="multipart",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())


class RetryFailedTests(ThumbnailFailureTestCase):
    def _park(self, file_obj):
        from workspace.files.services.thumbnail_failures import (
            MAX_THUMBNAIL_ATTEMPTS,
            record_failure,
        )

        for _ in range(MAX_THUMBNAIL_ATTEMPTS):
            record_failure(file_obj, ValueError("boom"))

    def test_retry_failed_purges_the_rows_and_rescans(self):
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        f = self._make_valid_image()
        self._park(f)
        self.assertEqual(generate_missing_thumbnails()["total"], 0)

        stats = generate_missing_thumbnails(retry_failed=True)

        self.assertEqual(stats["generated"], 1)
        self.assertFalse(ThumbnailFailure.objects.filter(file=f).exists())

    def test_default_pass_still_respects_the_parking(self):
        from workspace.files.services.thumbnails import generate_missing_thumbnails

        self._park(self._make_valid_image())

        self.assertEqual(generate_missing_thumbnails()["total"], 0)


class RetryFailedEndpointTests(ThumbnailFailureAPITestCase):
    def test_staff_caller_forwards_the_flag_to_the_task(self):
        from unittest.mock import patch

        from rest_framework import status

        self.client.force_authenticate(user=self.staff)

        with patch("workspace.files.tasks.generate_thumbnails.delay") as delay:
            delay.return_value.id = "task-1"
            resp = self.client.post(
                "/api/v1/thumbnails/generate",
                {"retry_failed": True},
                format="json",
            )

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        delay.assert_called_once_with(retry_failed=True)

    def test_non_staff_caller_may_not_unpark(self):
        # clear_all_failures() is global and cross-tenant: without this gate any
        # authenticated user could force a full re-decode of every known-broken
        # image in the deployment.
        from unittest.mock import patch

        from rest_framework import status

        with patch("workspace.files.tasks.generate_thumbnails.delay") as delay:
            resp = self.client.post(
                "/api/v1/thumbnails/generate",
                {"retry_failed": True},
                format="json",
            )

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        delay.assert_not_called()

    def test_non_staff_caller_may_still_trigger_a_plain_pass(self):
        from unittest.mock import patch

        from rest_framework import status

        with patch("workspace.files.tasks.generate_thumbnails.delay") as delay:
            delay.return_value.id = "task-1"
            resp = self.client.post("/api/v1/thumbnails/generate", {}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        delay.assert_called_once_with(retry_failed=False)

    def test_endpoint_reads_the_string_form_of_the_flag(self):
        # Pins the is_truthy requirement: "false" is a non-empty string and
        # would be truthy under plain Python truthiness, silently inverting
        # the caller's intent.
        from unittest.mock import patch

        with patch("workspace.files.tasks.generate_thumbnails.delay") as delay:
            delay.return_value.id = "task-1"
            self.client.post(
                "/api/v1/thumbnails/generate",
                {"retry_failed": "false"},
                format="json",
            )

        delay.assert_called_once_with(retry_failed=False)

    def test_endpoint_defaults_the_flag_to_false(self):
        from unittest.mock import patch

        from rest_framework import status

        with patch("workspace.files.tasks.generate_thumbnails.delay") as delay:
            delay.return_value.id = "task-1"
            resp = self.client.post("/api/v1/thumbnails/generate")

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        delay.assert_called_once_with(retry_failed=False)

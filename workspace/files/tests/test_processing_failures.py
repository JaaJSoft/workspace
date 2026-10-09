"""Tests for the processors' failure ledger, with thumbnails as the processor.

A file a processor can never handle must stop being read again by the hourly
catch-up after a bounded number of attempts, while a file that fails
transiently - or gets repaired - must still be picked up. The ledger is kept
per processor: a file parked by one keeps going through the others.
"""

import io
import logging
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from workspace.files.models import ProcessingFailure
from workspace.files.services import FileService
from workspace.files.services.thumbnails.generation import get_thumbnail_path

from .catch_up import run_catch_up

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


class ProcessingFailureTestCase(ThumbnailFileMixin, TestCase):
    """Base for the service-level tests."""


class ProcessingFailureAPITestCase(ThumbnailFileMixin, APITestCase):
    """Base for the tests that go through a real REST endpoint."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)


class ProcessingFailureModelTests(ProcessingFailureTestCase):
    def test_only_one_failure_row_per_file_and_processor(self):
        f = self._make_broken_image()
        ProcessingFailure.objects.create(
            processor="thumbnails", file=f, attempts=1, last_attempt_at=timezone.now()
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcessingFailure.objects.create(
                processor="thumbnails",
                file=f,
                attempts=1,
                last_attempt_at=timezone.now(),
            )

    def test_each_processor_keeps_its_own_row(self):
        f = self._make_broken_image()
        for processor in ("thumbnails", "media_info"):
            ProcessingFailure.objects.create(
                processor=processor, file=f, attempts=1, last_attempt_at=timezone.now()
            )

        self.assertEqual(ProcessingFailure.objects.filter(file=f).count(), 2)

    def test_deleting_the_file_removes_the_failure_row(self):
        f = self._make_broken_image()
        ProcessingFailure.objects.create(
            processor="thumbnails", file=f, attempts=2, last_attempt_at=timezone.now()
        )

        # File.delete() soft-deletes by default (an UPDATE, not a DELETE), so
        # the FK CASCADE never fires; hard=True forces a real row delete, per
        # the convention used by every other satellite-table cascade test
        # (test_links.py, test_sharing.py, test_tags.py, test_share_links.py).
        f.delete(hard=True)

        self.assertEqual(ProcessingFailure.objects.count(), 0)


class RecordFailureTests(ProcessingFailureTestCase):
    def test_first_failure_creates_a_row_with_one_attempt(self):
        from workspace.files.services.processing_failures import record_failure

        f = self._make_broken_image()

        record_failure(f, "thumbnails", ValueError("boom"))

        row = ProcessingFailure.objects.get(file=f)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.last_error, "boom")

    def test_repeated_failures_increment_the_same_row(self):
        from workspace.files.services.processing_failures import record_failure

        f = self._make_broken_image()

        record_failure(f, "thumbnails", ValueError("first"))
        record_failure(f, "thumbnails", ValueError("second"))

        self.assertEqual(ProcessingFailure.objects.filter(file=f).count(), 1)
        row = ProcessingFailure.objects.get(file=f)
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.last_error, "second")

    def test_last_attempt_at_moves_forward_on_each_failure(self):
        # Pins the "no auto_now" decision: auto_now never fires on a queryset
        # .update(), so the timestamp would freeze at the first attempt.
        from workspace.files.services.processing_failures import record_failure

        f = self._make_broken_image()
        record_failure(f, "thumbnails", ValueError("first"))
        first_seen = ProcessingFailure.objects.get(file=f).last_attempt_at

        record_failure(f, "thumbnails", ValueError("second"))

        self.assertGreater(
            ProcessingFailure.objects.get(file=f).last_attempt_at, first_seen
        )

    def test_long_error_is_truncated_to_the_column_width(self):
        from workspace.files.services.processing_failures import record_failure

        f = self._make_broken_image()

        record_failure(f, "thumbnails", ValueError("x" * 500))

        self.assertEqual(len(ProcessingFailure.objects.get(file=f).last_error), 200)

    def test_clear_failure_removes_the_row(self):
        from workspace.files.services.processing_failures import (
            clear_failure,
            record_failure,
        )

        f = self._make_broken_image()
        record_failure(f, "thumbnails", ValueError("boom"))

        clear_failure(f, "thumbnails")

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_clear_failure_is_a_noop_without_a_row(self):
        from workspace.files.services.processing_failures import clear_failure

        f = self._make_valid_image()

        clear_failure(f, "thumbnails")  # must not raise

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_parked_file_ids_holds_only_files_at_the_budget(self):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            parked_file_ids,
        )

        still_trying = self._make_broken_image("a.jpg")
        parked = self._make_broken_image("b.jpg")
        ProcessingFailure.objects.create(
            processor="thumbnails",
            file=still_trying,
            attempts=MAX_ATTEMPTS - 1,
            last_attempt_at=timezone.now(),
        )
        ProcessingFailure.objects.create(
            processor="thumbnails",
            file=parked,
            attempts=MAX_ATTEMPTS,
            last_attempt_at=timezone.now(),
        )

        ids = [row["file_id"] for row in parked_file_ids("thumbnails")]

        self.assertEqual(ids, [parked.uuid])

    def test_parked_file_ids_releases_a_file_once_the_retry_window_lapses(self):
        from datetime import timedelta

        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            PARKED_RETRY_AFTER,
            parked_file_ids,
        )

        stale = self._make_broken_image("stale.jpg")
        ProcessingFailure.objects.create(
            processor="thumbnails",
            file=stale,
            attempts=MAX_ATTEMPTS,
            last_attempt_at=timezone.now() - PARKED_RETRY_AFTER - timedelta(minutes=1),
        )

        self.assertEqual(list(parked_file_ids("thumbnails")), [])

    def test_clear_failure_leaves_the_other_processors_rows(self):
        from workspace.files.services.processing_failures import (
            clear_failure,
            record_failure,
        )

        f = self._make_broken_image()
        record_failure(f, "thumbnails", ValueError("boom"))
        record_failure(f, "media_info", ValueError("boom"))

        clear_failure(f, "thumbnails")

        self.assertEqual(
            list(ProcessingFailure.objects.values_list("processor", flat=True)),
            ["media_info"],
        )

    def test_clear_failures_drops_every_processor_row_of_the_file(self):
        from workspace.files.services.processing_failures import (
            clear_failures,
            record_failure,
        )

        f = self._make_broken_image("a.jpg")
        other = self._make_broken_image("b.jpg")
        record_failure(f, "thumbnails", ValueError("boom"))
        record_failure(f, "media_info", ValueError("boom"))
        record_failure(other, "thumbnails", ValueError("boom"))

        clear_failures(f)

        self.assertEqual(
            list(ProcessingFailure.objects.values_list("file_id", flat=True)),
            [other.pk],
        )

    def test_parked_file_ids_is_scoped_to_the_processor(self):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            parked_file_ids,
        )

        f = self._make_broken_image()
        ProcessingFailure.objects.create(
            processor="media_info",
            file=f,
            attempts=MAX_ATTEMPTS,
            last_attempt_at=timezone.now(),
        )

        self.assertEqual(list(parked_file_ids("thumbnails")), [])
        self.assertEqual(
            [row["file_id"] for row in parked_file_ids("media_info")], [f.uuid]
        )


class RunProcessorBookkeepingTests(ProcessingFailureTestCase):
    def _run(self, file_obj):
        from workspace.files.services.processors import get_processor, run_processor

        return run_processor(get_processor("thumbnails"), file_obj)

    def test_failed_generation_raises_for_the_runner(self):
        from workspace.files.services.thumbnails.generation import generate_thumbnail

        f = self._make_broken_image()

        with self.assertRaises(Exception):
            generate_thumbnail(f)

    def test_failed_generation_records_an_attempt(self):
        f = self._make_broken_image()

        self.assertFalse(self._run(f))

        row = ProcessingFailure.objects.get(file=f, processor="thumbnails")
        self.assertEqual(row.attempts, 1)
        self.assertTrue(row.last_error, "the decoder error should be recorded")

    def test_successful_generation_clears_a_previous_failure(self):
        from workspace.files.services.processing_failures import record_failure

        f = self._make_valid_image()
        record_failure(f, "thumbnails", ValueError("a previous transient failure"))

        self.assertTrue(self._run(f))

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_skipped_generation_records_nothing(self):
        # A type outside THUMBNAIL_LABELS returns False without ever decoding.
        # That is not a failure and must not consume an attempt.
        f = self._make_file("doc.pdf", b"%PDF-1.4", "pdf", "application/pdf")

        self.assertFalse(self._run(f))

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_bookkeeping_error_does_not_escape_a_failed_run(self):
        # The file can be hard-deleted between the catch-up's fetch and this
        # write, making the FK insert fail. Escaping here would abandon the
        # rest of the pipeline, which runs with max_retries=0.
        f = self._make_broken_image()

        with patch(
            "workspace.files.services.processing_failures.record_failure",
            side_effect=IntegrityError("the file row is gone"),
        ):
            self.assertFalse(self._run(f))

    def test_bookkeeping_error_does_not_sink_a_successful_run(self):
        f = self._make_valid_image()

        with patch(
            "workspace.files.services.processing_failures.clear_failure",
            side_effect=IntegrityError("the file row is gone"),
        ):
            self.assertTrue(self._run(f))

        self.assertTrue(default_storage.exists(get_thumbnail_path(f.uuid)))


class CatchUpParkingTests(ProcessingFailureTestCase):
    def test_permanently_failing_file_is_not_retried_forever(self):
        """The regression test for issue #426.

        Against the pre-fix code, pass 4 attempts the file again - which is the
        whole bug.
        """
        from workspace.files.services.processing_failures import MAX_ATTEMPTS

        f = self._make_broken_image()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.assertEqual(
                run_catch_up("thumbnails"), 1, f"pass {attempt} should attempt it"
            )
            self.assertEqual(ProcessingFailure.objects.get(file=f).attempts, attempt)

        # Created only now: an image present from the start would have been
        # given its thumbnail by pass 1 and stopped being pending.
        healthy = self._make_valid_image()

        self.assertEqual(
            run_catch_up("thumbnails"), 1, "only the healthy file may be attempted"
        )
        healthy.refresh_from_db()
        self.assertTrue(healthy.has_thumbnail)
        self.assertEqual(ProcessingFailure.objects.get(file=f).attempts, MAX_ATTEMPTS)

    def test_file_parked_longer_than_the_retry_window_is_attempted_again(self):
        from datetime import timedelta

        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            PARKED_RETRY_AFTER,
        )

        f = self._make_valid_image()
        ProcessingFailure.objects.create(
            processor="thumbnails",
            file=f,
            attempts=MAX_ATTEMPTS,
            last_attempt_at=timezone.now() - PARKED_RETRY_AFTER - timedelta(minutes=1),
        )

        self.assertEqual(
            run_catch_up("thumbnails"), 1, "a stale parking must not be permanent"
        )
        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)
        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_transient_failure_is_retried_and_then_forgotten(self):
        from workspace.files.services.processing_failures import record_failure

        f = self._make_valid_image()
        record_failure(f, "thumbnails", OSError("storage was briefly unreachable"))

        run_catch_up("thumbnails")

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())
        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)

    def test_a_file_given_its_thumbnail_is_no_longer_pending(self):
        self._make_valid_image()

        self.assertEqual(run_catch_up("thumbnails"), 1)
        self.assertEqual(run_catch_up("thumbnails"), 0)


class PipelineBudgetTests(ProcessingFailureTestCase):
    """The upload pipeline is the first consumer of the budget in prod.

    Running it directly is deliberate: the real dispatch is wrapped in
    transaction.on_commit, which never runs under TestCase.
    """

    def test_pipeline_failure_spends_one_attempt_of_the_budget(self):
        from workspace.files.services.processing_failures import MAX_ATTEMPTS
        from workspace.files.services.processors import run_pipeline

        f = self._make_broken_image()

        run_pipeline(f.uuid)

        self.assertEqual(ProcessingFailure.objects.get(file=f).attempts, 1)

        self.assertEqual(run_catch_up("thumbnails"), 1, "attempt 2 of 3")
        self.assertEqual(run_catch_up("thumbnails"), 1, "attempt 3 of 3")
        self.assertEqual(run_catch_up("thumbnails"), 0, "budget spent")
        self.assertEqual(ProcessingFailure.objects.get(file=f).attempts, MAX_ATTEMPTS)

    def test_a_failing_processor_still_lets_the_file_settle(self):
        from workspace.files.models import File
        from workspace.files.services.processors import run_pipeline

        f = self._make_broken_image()

        self.assertEqual(run_pipeline(f.uuid), File.ProcessingStatus.READY)

        f.refresh_from_db()
        self.assertEqual(f.processing_status, File.ProcessingStatus.READY)


class ContentReplacementResetsBudgetTests(ProcessingFailureTestCase):
    def test_update_content_clears_the_failure_row(self):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image()
        for _ in range(MAX_ATTEMPTS):
            record_failure(f, "thumbnails", ValueError("boom"))

        FileService.update_content(
            f,
            ContentFile(_image_bytes(), name="broken.jpg"),
            name="broken.jpg",
            mime_type="image/jpeg",
        )

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_replace_content_storage_clears_the_failure_row(self):
        # The WebDAV PUT path. Pinned separately because it is a distinct
        # method, not a wrapper around update_content.
        from workspace.files.services.processing_failures import record_failure

        f = self._make_broken_image()
        record_failure(f, "thumbnails", ValueError("boom"))

        FileService.replace_content_storage(
            f, storage_path=f.content.name, size=f.size or 1, content_hash="0" * 64
        )

        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())

    def test_parked_file_is_scanned_again_after_its_content_is_replaced(self):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image()
        for _ in range(MAX_ATTEMPTS):
            record_failure(f, "thumbnails", ValueError("boom"))
        self.assertEqual(run_catch_up("thumbnails"), 0)

        FileService.update_content(
            f,
            ContentFile(_image_bytes(), name="broken.jpg"),
            name="broken.jpg",
            mime_type="image/jpeg",
        )

        self.assertEqual(run_catch_up("thumbnails"), 1)
        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)


class ContentReplacementViaApiTests(ProcessingFailureAPITestCase):
    """The unit tests above, backed by one pass through the real REST entry point."""

    def test_patching_a_parked_file_makes_it_eligible_again(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from rest_framework import status

        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image()
        for _ in range(MAX_ATTEMPTS):
            record_failure(f, "thumbnails", ValueError("boom"))

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
        self.assertFalse(ProcessingFailure.objects.filter(file=f).exists())


class RetryFailedTests(ProcessingFailureTestCase):
    def _park(self, file_obj):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
        )

        for _ in range(MAX_ATTEMPTS):
            record_failure(file_obj, "thumbnails", ValueError("boom"))

    def test_parked_file_is_not_pending(self):
        self._park(self._make_valid_image())

        self.assertEqual(run_catch_up("thumbnails"), 0)

    def test_clearing_the_ledger_makes_parked_files_pending_again(self):
        f = self._make_valid_image()
        self._park(f)

        ProcessingFailure.objects.all().delete()
        self.assertEqual(run_catch_up("thumbnails"), 1)
        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)


class RetryQueuesTheUnparkedFilesTests(ProcessingFailureTestCase):
    @patch("workspace.files.tasks.CATCH_UP_LIMIT", 1)
    def test_unparked_files_are_queued_even_behind_a_larger_backlog(self):
        """A pass is bounded: a retry that only queued one could leave the
        files the operator asked for behind the rest of the backlog."""
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
            retry_failures,
        )
        from workspace.files.tasks import catch_up, catch_up_file

        self._make_valid_image("older.jpg")
        parked = self._make_broken_image()
        for _ in range(MAX_ATTEMPTS):
            record_failure(parked, "thumbnails", ValueError("boom"))

        with (
            patch.object(catch_up_file, "apply_async") as queue,
            patch.object(
                catch_up, "delay", side_effect=lambda **kw: catch_up.apply(kwargs=kw)
            ),
        ):
            self.assertEqual(
                retry_failures(ProcessingFailure.objects.filter(file=parked)), 1
            )

        self.assertIn(
            ["thumbnails", str(parked.pk)],
            [c.kwargs["args"] for c in queue.call_args_list],
        )
        self.assertFalse(ProcessingFailure.objects.exists())


class RetryFailedEndpointTests(ProcessingFailureAPITestCase):
    def _post(self, *args, **kwargs):
        with (
            patch("workspace.files.tasks.catch_up.delay") as delay,
            patch("workspace.files.tasks.catch_up_file.apply_async") as self.queue,
        ):
            delay.return_value.id = "task-1"
            resp = self.client.post("/api/v1/thumbnails/generate", *args, **kwargs)
        return resp, delay

    def _park(self):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image()
        for _ in range(MAX_ATTEMPTS):
            record_failure(f, "thumbnails", ValueError("boom"))
        return f

    def test_staff_caller_leaves_the_other_processors_parked(self):
        from workspace.files.services.processing_failures import (
            MAX_ATTEMPTS,
            record_failure,
        )

        f = self._make_broken_image("other.jpg")
        for _ in range(MAX_ATTEMPTS):
            record_failure(f, "media_info", ValueError("boom"))
        self.client.force_authenticate(user=self.staff)

        self._post({"retry_failed": True}, format="json")

        self.assertTrue(
            ProcessingFailure.objects.filter(processor="media_info").exists()
        )

    def test_staff_caller_unparks_every_file_and_queues_the_catch_up(self):
        from rest_framework import status

        parked = self._park()
        self.client.force_authenticate(user=self.staff)

        resp, delay = self._post({"retry_failed": True}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(ProcessingFailure.objects.exists())
        self.assertEqual(
            [c.kwargs["args"] for c in self.queue.call_args_list],
            [["thumbnails", str(parked.pk)]],
        )
        delay.assert_called_once_with(names=["thumbnails"])

    def test_non_staff_caller_may_not_unpark(self):
        # The retry is global and cross-tenant: without this gate any
        # authenticated user could force a full re-decode of every known-broken
        # image in the deployment.
        from rest_framework import status

        self._park()

        resp, delay = self._post({"retry_failed": True}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(ProcessingFailure.objects.exists())
        delay.assert_not_called()

    def test_non_staff_caller_may_still_trigger_a_plain_pass(self):
        from rest_framework import status

        self._park()

        resp, delay = self._post({}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(ProcessingFailure.objects.exists())
        delay.assert_called_once_with(names=["thumbnails"])

    def test_endpoint_reads_the_string_form_of_the_flag(self):
        # Pins the is_truthy requirement: "false" is a non-empty string and
        # would be truthy under plain Python truthiness, silently inverting
        # the caller's intent.
        self._park()
        self.client.force_authenticate(user=self.staff)

        self._post({"retry_failed": "false"}, format="json")

        self.assertTrue(ProcessingFailure.objects.exists())

    def test_endpoint_defaults_the_flag_to_false(self):
        from rest_framework import status

        self._park()
        self.client.force_authenticate(user=self.staff)

        resp, _ = self._post()

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(ProcessingFailure.objects.exists())


class FailureCountTests(TestCase):
    def test_counts_one_row_per_failing_file_and_processor(self):
        from workspace.files.models import File
        from workspace.files.services.processing_failures import failure_count

        user = User.objects.create_user(username="parkedcount", password="pw")
        f = File.objects.create(
            owner=user, name="broken.jpg", node_type=File.NodeType.FILE
        )
        self.assertEqual(failure_count(), 0)
        for processor in ("thumbnails", "media_info"):
            ProcessingFailure.objects.create(
                file=f, processor=processor, attempts=1, last_attempt_at=timezone.now()
            )
        self.assertEqual(failure_count(), 2)
        self.assertEqual(failure_count("thumbnails"), 1)

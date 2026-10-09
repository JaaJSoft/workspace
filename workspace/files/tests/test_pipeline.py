"""The upload pipeline: every processor run on a file whose content landed."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.files.models import File, FileEvent, FileScan, ProcessingFailure
from workspace.files.services import FileService
from workspace.files.services import processors as registry
from workspace.files.services.event_dispatch import _HANDLERS
from workspace.files.services.processing_failures import MAX_ATTEMPTS
from workspace.files.services.processors import (
    STALLED_AFTER,
    queue_pipeline_for_event,
    queue_stalled,
    register_processor,
    run_pipeline,
)
from workspace.files.tasks import catch_up, catch_up_file, process_file

from .catch_up import run_catch_up

User = get_user_model()
Status = File.ProcessingStatus


class FakeProcessor:
    """Records what the pipeline asked of it; .txt files only."""

    def __init__(self, journal, name, *, fails=False):
        self.journal = journal
        self.name = name
        self.fails = fails

    def applies_to(self, file_obj):
        return file_obj.name.endswith(".txt")

    def process(self, file_obj):
        self.journal.append((self.name, "process", file_obj.pk))
        if self.fails:
            raise ValueError(f"{self.name} cannot read this")
        return True

    def forget(self, file_obj):
        self.journal.append((self.name, "forget", file_obj.pk))

    def enqueue(self, file_obj):
        self.journal.append((self.name, "enqueue", file_obj.pk))

    def pending(self, *, reanalyze=False):
        return File.objects.filter(node_type=File.NodeType.FILE, name__endswith=".txt")

    def register(self, *, order=registry.DEFAULT_ORDER, queued=False):
        register_processor(
            self.name,
            applies_to=self.applies_to,
            forget=self.forget,
            enqueue=self.enqueue if queued else None,
            pending=self.pending,
            process=self.process,
            order=order,
        )
        return self


class PipelineTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        registered = patch.dict(registry._PROCESSORS, clear=True)
        registered.start()
        self.addCleanup(registered.stop)
        self.journal = []

    def _processor(self, name, **kwargs):
        register = {k: kwargs.pop(k) for k in ("order", "queued") if k in kwargs}
        return FakeProcessor(self.journal, name, **kwargs).register(**register)

    def _upload(self, name="a.txt", body=b"x"):
        return FileService.create_file(
            owner=self.user, name=name, content=ContentFile(body, name=name)
        )


class WritePathStatusTests(PipelineTestCase):
    def _status(self, file_obj):
        return File.objects.values_list("processing_status", flat=True).get(
            pk=file_obj.pk
        )

    def test_an_upload_is_pending(self):
        self.assertEqual(self._status(self._upload()), Status.PENDING)

    def test_a_file_created_empty_is_ready(self):
        f = FileService.create_file(owner=self.user, name="empty.txt")

        self.assertEqual(self._status(f), Status.READY)

    def test_a_folder_is_ready(self):
        folder = FileService.create_folder(owner=self.user, name="docs")

        self.assertEqual(self._status(folder), Status.READY)

    def test_replacing_the_content_makes_the_file_pending_again(self):
        f = self._upload()
        File.objects.filter(pk=f.pk).update(processing_status=Status.READY)

        FileService.update_content(f, ContentFile(b"new", name="a.txt"), name="a.txt")

        self.assertEqual(self._status(f), Status.PENDING)

    def test_a_streamed_replacement_makes_the_file_pending_again(self):
        f = self._upload()
        File.objects.filter(pk=f.pk).update(processing_status=Status.READY)

        FileService.replace_content_storage(
            f, storage_path=f.content.name, size=1, content_hash="0" * 64
        )

        self.assertEqual(self._status(f), Status.PENDING)

    def test_emptying_the_content_leaves_nothing_to_process(self):
        f = self._upload()

        FileService.clear_content(f)

        self.assertEqual(self._status(f), Status.READY)

    def test_replacing_the_content_drops_every_processor_failure(self):
        f = self._upload()
        for processor in ("thumbnails", "media_info"):
            ProcessingFailure.objects.create(
                file=f,
                processor=processor,
                attempts=MAX_ATTEMPTS,
                last_attempt_at=timezone.now(),
            )

        FileService.update_content(f, ContentFile(b"new", name="a.txt"), name="a.txt")

        self.assertFalse(ProcessingFailure.objects.exists())


class RunPipelineTests(PipelineTestCase):
    def test_runs_every_processor_in_order_and_settles_the_file(self):
        self._processor("late", order=200)
        self._processor("early", order=0)
        self._processor("middle")
        f = self._upload()

        self.assertEqual(run_pipeline(f.uuid), Status.READY)

        self.assertEqual(
            [name for name, _, _ in self.journal], ["early", "middle", "late"]
        )
        f.refresh_from_db()
        self.assertEqual(f.processing_status, Status.READY)

    def test_a_processor_that_does_not_apply_forgets_the_file(self):
        self._processor("notes")
        f = self._upload("photo.jpg")

        run_pipeline(f.uuid)

        self.assertEqual(self.journal, [("notes", "forget", f.pk)])

    def test_a_queued_processor_is_handed_the_file_instead_of_run(self):
        self._processor("faces", queued=True)
        f = self._upload()

        run_pipeline(f.uuid)

        self.assertEqual(self.journal, [("faces", "enqueue", f.pk)])

    def test_a_catch_up_only_processor_is_left_out(self):
        register_processor(
            "hash", pending=File.objects.none, process=self.fail_if_called
        )
        f = self._upload()

        self.assertEqual(run_pipeline(f.uuid), Status.READY)

    def fail_if_called(self, file_obj):
        raise AssertionError("the pipeline must not run a catch-up-only processor")

    def test_a_disabled_processor_is_left_out(self):
        register_processor(
            "off",
            applies_to=lambda f: True,
            pending=File.objects.none,
            process=self.fail_if_called,
            enabled=lambda: False,
        )
        f = self._upload()

        self.assertEqual(run_pipeline(f.uuid), Status.READY)

    def test_a_failing_processor_does_not_stop_the_next_one(self):
        self._processor("broken", order=0, fails=True)
        self._processor("healthy")
        f = self._upload()

        self.assertEqual(run_pipeline(f.uuid), Status.READY)

        self.assertIn(("healthy", "process", f.pk), self.journal)
        failure = ProcessingFailure.objects.get(file=f)
        self.assertEqual((failure.processor, failure.attempts), ("broken", 1))
        self.assertIn("cannot read this", failure.last_error)

    def test_a_replacement_mid_run_leaves_the_file_to_its_own_pipeline(self):
        """The write that replaced the content set the file back to pending and
        queued a pipeline of its own: this run must neither go on reading nor
        call the new bytes ready."""

        def replace_then_process(file_obj):
            FileService.update_content(
                file_obj, ContentFile(b"newer", name="a.txt"), name="a.txt"
            )
            self.journal.append(("first", "process", file_obj.pk))
            return True

        register_processor(
            "first",
            applies_to=lambda f: True,
            pending=File.objects.none,
            process=replace_then_process,
            order=0,
        )
        self._processor("second")
        f = self._upload()

        self.assertIsNone(run_pipeline(f.uuid))

        self.assertEqual(self.journal, [("first", "process", f.pk)])
        f.refresh_from_db()
        self.assertEqual(f.processing_status, Status.PENDING)

    def test_a_trashed_file_is_left_for_after_its_restore(self):
        self._processor("any")
        f = self._upload()
        FileService.soft_delete(f, acting_user=self.user)

        self.assertIsNone(run_pipeline(f.uuid))

        self.assertEqual(self.journal, [])
        f.refresh_from_db()
        self.assertEqual(f.processing_status, Status.PENDING)

    def test_nothing_to_run_on(self):
        self._processor("any")
        folder = FileService.create_folder(owner=self.user, name="docs")

        for file_uuid in (folder.uuid, "00000000-0000-0000-0000-000000000000", "nope"):
            with self.subTest(file_uuid=file_uuid):
                self.assertIsNone(run_pipeline(file_uuid))
        self.assertEqual(self.journal, [])

    def test_the_task_reports_the_settled_status(self):
        self._processor("any")
        f = self._upload()

        self.assertEqual(
            process_file.apply(args=[str(f.uuid)]).get(), {"status": Status.READY}
        )


class PipelineDispatchTests(PipelineTestCase):
    def test_subscribed_to_uploads_and_content_replacements(self):
        for action in (FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED):
            with self.subTest(action=action):
                self.assertIn(queue_pipeline_for_event, _HANDLERS[str(action)])

    def test_an_upload_queues_the_pipeline(self):
        f = self._upload()
        event = FileEvent(file=f, action=FileEvent.Action.CREATED)

        with patch.object(process_file, "delay") as delay:
            queue_pipeline_for_event(event)

        delay.assert_called_once_with(str(f.pk))

    def test_a_folder_queues_nothing(self):
        folder = FileService.create_folder(owner=self.user, name="docs")
        event = FileEvent(file=folder, action=FileEvent.Action.CREATED)

        with patch.object(process_file, "delay") as delay:
            queue_pipeline_for_event(event)

        delay.assert_not_called()

    def test_a_committed_upload_runs_the_whole_pipeline(self):
        self._processor("any")

        with self.captureOnCommitCallbacks(execute=True):
            f = self._upload()

        self.assertEqual(self.journal, [("any", "process", f.pk)])
        f.refresh_from_db()
        self.assertEqual(f.processing_status, Status.READY)


class StalledPipelineTests(PipelineTestCase):
    def _age(self, file_obj, by):
        File.objects.filter(pk=file_obj.pk).update(updated_at=timezone.now() - by)

    def _queued(self, **kwargs):
        with patch.object(process_file, "apply_async") as queue:
            count = queue_stalled(**kwargs)
        return count, [c.kwargs["args"] for c in queue.call_args_list]

    def test_a_file_left_pending_is_queued_again(self):
        f = self._upload()
        self._age(f, STALLED_AFTER + timedelta(minutes=1))

        self.assertEqual(self._queued(), (1, [[str(f.pk)]]))

    def test_a_file_left_processing_is_queued_again(self):
        f = self._upload()
        File.objects.filter(pk=f.pk).update(processing_status=Status.PROCESSING)
        self._age(f, STALLED_AFTER + timedelta(minutes=1))

        self.assertEqual(self._queued()[0], 1)

    def test_a_recent_upload_is_left_to_its_own_pipeline(self):
        self._upload()

        self.assertEqual(self._queued(), (0, []))

    def test_settled_and_trashed_files_are_left_alone(self):
        ready = self._upload("ready.txt")
        File.objects.filter(pk=ready.pk).update(processing_status=Status.READY)
        trashed = self._upload("trashed.txt")
        FileService.soft_delete(trashed, acting_user=self.user)
        for f in (ready, trashed):
            self._age(f, STALLED_AFTER + timedelta(minutes=1))

        self.assertEqual(self._queued(), (0, []))

    def test_the_hourly_pass_requeues_them(self):
        f = self._upload()
        self._age(f, STALLED_AFTER + timedelta(minutes=1))

        with (
            patch.object(process_file, "apply_async") as queue,
            patch.object(catch_up_file, "apply_async"),
        ):
            stats = catch_up.apply().get()

        self.assertEqual(stats["pipeline"], 1)
        self.assertEqual(queue.call_args.kwargs["args"], [str(f.pk)])
        self.assertIsNotNone(queue.call_args.kwargs["expires"])

    def test_a_pass_limited_to_named_processors_does_not(self):
        self._processor("any")
        f = self._upload()
        self._age(f, STALLED_AFTER + timedelta(minutes=1))

        with (
            patch.object(process_file, "apply_async") as queue,
            patch.object(catch_up_file, "apply_async"),
        ):
            stats = catch_up.apply(kwargs={"names": ["any"]}).get()

        self.assertNotIn("pipeline", stats)
        queue.assert_not_called()


class ParkingTests(PipelineTestCase):
    def test_a_file_failing_for_good_stops_being_retried(self):
        """Any registered processor gets the budget, not just thumbnails."""
        self._processor("broken", fails=True)
        f = self._upload()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.assertEqual(run_catch_up("broken"), 1, f"pass {attempt}")

        self.assertEqual(run_catch_up("broken"), 0, "budget spent")
        self.assertEqual(
            ProcessingFailure.objects.get(file=f, processor="broken").attempts,
            MAX_ATTEMPTS,
        )
        self.assertEqual(
            registry.get_processor("broken").pending_files(reanalyze=True).count(),
            1,
            "a forced reanalysis still reaches it",
        )

    def test_a_file_parked_by_one_processor_stays_pending_for_the_others(self):
        self._processor("broken", fails=True)
        self._processor("healthy")
        f = self._upload()
        ProcessingFailure.objects.create(
            file=f,
            processor="broken",
            attempts=MAX_ATTEMPTS,
            last_attempt_at=timezone.now(),
        )

        self.assertEqual(run_catch_up("healthy"), 1)


class ProcessingStatusApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="api", password="p")
        self.client.force_authenticate(user=self.user)

    def _status(self, file_obj):
        resp = self.client.get(f"/api/v1/files/{file_obj.uuid}")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["processing_status"]

    def test_an_upload_reads_pending_until_its_pipeline_ran(self):
        f = FileService.create_file(
            owner=self.user, name="a.txt", content=ContentFile(b"x", name="a.txt")
        )
        self.assertEqual(self._status(f), "pending")

        run_pipeline(f.uuid)

        self.assertEqual(self._status(f), "ready")

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_file_reads_quarantined(self):
        f = FileService.create_file(
            owner=self.user, name="a.txt", content=ContentFile(b"x", name="a.txt")
        )
        File.objects.filter(pk=f.pk).update(processing_status=Status.READY)
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertEqual(self._status(f), "quarantined")

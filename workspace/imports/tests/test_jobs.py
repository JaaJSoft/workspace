import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File
from workspace.imports.importers.base import Outcome
from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem
from workspace.imports.services import jobs as svc
from workspace.imports.services.progress import PENDING_EVENTS_KEY
from workspace.imports.sse_provider import ImportsSSEProvider
from workspace.imports.tasks import purge_old_jobs, run_import_job
from workspace.notifications.models import Notification

from .fakes import fake_provider

User = get_user_model()


class JobsTestCase(TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._media = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media.enable()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.provider = fake_provider()
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Cloud",
            base_url="https://x/dav",
            username="a",
        )
        cache.clear()

    def tearDown(self):
        self._media.disable()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        cache.clear()


class CreateJobTests(JobsTestCase):
    def test_creates_a_pending_job_with_validated_options_and_enqueues_it(self):
        with patch("workspace.imports.services.jobs._enqueue") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                job = svc.create_job(
                    self.user, self.conn, ["files"], {"files": {"source_path": "Docs/"}}
                )
        enqueue.assert_called_once_with(job)
        self.assertEqual(job.status, ImportJob.Status.PENDING)
        self.assertEqual(job.kinds, ["files"])
        self.assertEqual(
            job.options["files"],
            {
                "source_path": "/Docs",
                "destination": None,
                "on_conflict": "rename",
                "create_root_folder": True,
            },
        )

    def test_rejects_empty_unknown_and_unsupported_kinds(self):
        with self.assertRaises(svc.InvalidJob):
            svc.create_job(self.user, self.conn, [])
        with self.assertRaisesRegex(
            svc.InvalidJob, "Nothing knows how to import photos"
        ):
            svc.create_job(self.user, self.conn, ["photos"])
        self.provider.kinds = frozenset({"files", "calendar"})
        try:
            with self.assertRaisesRegex(
                svc.InvalidJob, "Nothing knows how to import calendar"
            ):
                svc.create_job(self.user, self.conn, ["calendar"])
        finally:
            self.provider.kinds = frozenset({"files"})

    def test_rejects_invalid_options_per_kind(self):
        with self.assertRaises(svc.InvalidJobOptions) as caught:
            svc.create_job(
                self.user, self.conn, ["files"], {"files": {"on_conflict": "explode"}}
            )
        self.assertIn("on_conflict", caught.exception.errors["files"])

    def test_destination_must_be_the_users_folder(self):
        other = User.objects.create_user(username="bob", password="pw")
        theirs = File.objects.create(
            owner=other, name="x", node_type=File.NodeType.FOLDER
        )
        with self.assertRaises(svc.InvalidJobOptions):
            svc.create_job(
                self.user,
                self.conn,
                ["files"],
                {"files": {"destination": str(theirs.uuid)}},
            )

    def test_one_running_job_per_connection(self):
        ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.RUNNING
        )
        with self.assertRaises(svc.JobAlreadyRunning):
            svc.create_job(self.user, self.conn, ["files"])


class RunJobTests(JobsTestCase):
    def _pending(self):
        return ImportJob.objects.create(
            connection=self.conn, kinds=["files"], options={"files": {}}
        )

    def test_runs_to_completion_and_notifies(self):
        job = self._pending()
        self.assertEqual(svc.run_job(job.pk), "done")
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.COMPLETED)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.stats["files"]["files"], 3)
        self.assertEqual(job.stats["files"]["phase"], "done")

        notif = Notification.objects.get(recipient=self.user)
        self.assertEqual(notif.origin, "imports")
        self.assertIn("finished", notif.title)
        self.assertIn("3 files", notif.body)
        self.assertEqual(notif.url, f"/imports?job={job.pk}")

    def test_progress_events_reach_the_owner_mailbox(self):
        job = self._pending()
        svc.run_job(job.pk)
        events = cache.get(PENDING_EVENTS_KEY.format(user_id=self.user.id))
        self.assertEqual(len(events), 1)  # newest payload supersedes the older ones
        self.assertEqual(events[0]["type"], "imports.job")
        self.assertEqual(events[0]["status"], "completed")
        provider = ImportsSSEProvider(self.user, None)
        polled = provider.poll("dirty")
        self.assertEqual(polled[0][0], "imports.job")
        self.assertEqual(provider.poll("dirty"), [])

    def test_failed_job_records_the_reason(self):
        self.provider.fail_list.add("/")
        job = self._pending()
        self.assertEqual(svc.run_job(job.pk), "failed")
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertIn("Could not list '/'", job.error)
        self.assertIn("failed", Notification.objects.get().title)

    def test_unexpected_exception_fails_the_job_without_propagating(self):
        job = self._pending()
        with patch(
            "workspace.imports.services.jobs._run_slice",
            side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(svc.run_job(job.pk), "failed")
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertIn("Unexpected error", job.error)

    def test_only_pending_or_running_jobs_run(self):
        job = self._pending()
        job.status = ImportJob.Status.COMPLETED
        job.save()
        self.assertEqual(svc.run_job(job.pk), "skipped")
        self.assertEqual(svc.run_job("00000000-0000-0000-0000-000000000000"), "skipped")

    def test_a_second_worker_is_turned_away_by_the_lock(self):
        job = self._pending()
        with patch("workspace.imports.services.jobs.task_lock") as lock:
            lock.return_value.__enter__.return_value = False
            self.assertEqual(svc.run_job(job.pk), "skipped")

    def test_paused_slice_keeps_the_job_running_and_its_stats(self):
        job = self._pending()
        with override_settings(IMPORTS_BATCH_SECONDS=0):
            self.assertEqual(svc.run_job(job.pk), "paused")
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.RUNNING)
        self.assertEqual(job.stats["files"]["phase"], "listing")
        self.assertEqual(svc.run_job(job.pk), "done")

    def test_cancel_requested_ends_as_cancelled(self):
        job = self._pending()
        ImportJob.objects.filter(pk=job.pk).update(
            status=ImportJob.Status.RUNNING, cancel_requested_at=timezone.now()
        )
        self.assertEqual(svc.run_job(job.pk), "cancelled")
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.CANCELLED)
        self.assertIn("cancelled", Notification.objects.get().title)


class CancelRetryPurgeTests(JobsTestCase):
    def test_cancel_pending_job_ends_it_immediately(self):
        job = ImportJob.objects.create(connection=self.conn, kinds=["files"])
        job = svc.cancel_job(job)
        self.assertEqual(job.status, ImportJob.Status.CANCELLED)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(svc.run_job(job.pk), "skipped")

    def test_cancel_running_job_only_flags_it(self):
        job = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.RUNNING
        )
        job = svc.cancel_job(job)
        self.assertEqual(job.status, ImportJob.Status.RUNNING)
        self.assertIsNotNone(job.cancel_requested_at)

    def test_cancel_finished_job_is_refused(self):
        job = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.COMPLETED
        )
        with self.assertRaises(svc.InvalidJob):
            svc.cancel_job(job)

    def test_retry_creates_a_new_job_with_the_same_settings(self):
        failed = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.FAILED,
            options={
                "files": {
                    "source_path": "/Docs",
                    "destination": None,
                    "on_conflict": "skip",
                    "create_root_folder": True,
                }
            },
        )
        with patch("workspace.imports.services.jobs._enqueue"):
            new = svc.retry_job(failed)
        self.assertNotEqual(new.pk, failed.pk)
        self.assertEqual(new.options, failed.options)
        self.assertEqual(new.status, ImportJob.Status.PENDING)

    def test_retry_requires_a_failed_or_cancelled_job(self):
        done = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.COMPLETED
        )
        with self.assertRaises(svc.InvalidJob):
            svc.retry_job(done)

    @override_settings(IMPORTS_JOB_RETENTION_DAYS=30)
    def test_purge_removes_old_terminal_jobs_and_their_items(self):
        old = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.COMPLETED,
            finished_at=timezone.now() - timedelta(days=31),
        )
        ImportJobItem.objects.create(
            job=old, kind="files", remote_id="/a", status=ImportJobItem.Status.DONE
        )
        recent = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.COMPLETED,
            finished_at=timezone.now() - timedelta(days=2),
        )
        running = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.RUNNING,
            finished_at=None,
        )
        self.assertEqual(
            purge_old_jobs.apply().result, {"deleted": 2}
        )  # job + its item
        self.assertEqual(
            set(ImportJob.objects.values_list("pk", flat=True)), {recent.pk, running.pk}
        )
        self.assertFalse(ImportJobItem.objects.exists())


class TaskTests(JobsTestCase):
    def test_eager_task_loops_over_paused_slices(self):
        with patch(
            "workspace.imports.services.jobs.run_job",
            side_effect=["paused", "paused", "done"],
        ) as run:
            result = run_import_job.apply(
                args=["00000000-0000-0000-0000-000000000000"]
            ).result
        self.assertEqual(result, {"status": "done"})
        self.assertEqual(run.call_count, 3)

    def test_worker_task_re_enqueues_itself_when_paused(self):
        with (
            patch("workspace.imports.services.jobs.run_job", return_value="paused"),
            patch.object(run_import_job, "apply_async") as apply_async,
        ):
            # Bypass .apply() so the request is not flagged eager.
            result = run_import_job.run("00000000-0000-0000-0000-000000000000")
        self.assertEqual(result, {"status": "paused"})
        apply_async.assert_called_once()

    def test_outcome_enum_round_trip(self):
        self.assertEqual(Outcome("paused"), Outcome.PAUSED)

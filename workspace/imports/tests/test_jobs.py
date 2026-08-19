import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.core.sse_registry import drain_user_events
from workspace.files.models import File
from workspace.imports.importers.base import Outcome
from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem
from workspace.imports.services import jobs as svc
from workspace.imports.sse_provider import ImportsSSEProvider
from workspace.imports.tasks import purge_old_jobs, recover_stale_jobs, run_import_job
from workspace.notifications.models import Notification

from .fakes import fake_provider

User = get_user_model()


@override_settings(IMPORTS_ALLOWED_HOSTS=["x", "y"])
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

    def test_source_path_refuses_dot_segments(self):
        with self.assertRaises(svc.InvalidJobOptions) as caught:
            svc.create_job(
                self.user, self.conn, ["files"], {"files": {"source_path": "/a/../b"}}
            )
        self.assertIn("source_path", caught.exception.errors["files"])

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

    def test_the_guard_is_a_database_constraint_not_a_lookup(self):
        ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.PENDING
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ImportJob.objects.create(
                connection=self.conn, kinds=["files"], status=ImportJob.Status.RUNNING
            )
        ImportJob.objects.filter(connection=self.conn).update(
            status=ImportJob.Status.FAILED
        )
        ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.RUNNING
        )


class RunJobTests(JobsTestCase):
    def _pending(self):
        return ImportJob.objects.create(
            connection=self.conn, kinds=["files"], options={"files": {}}
        )

    def test_runs_to_completion_and_notifies(self):
        job = self._pending()
        self.assertIs(svc.run_job(job.pk), Outcome.DONE)
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
        provider = ImportsSSEProvider(self.user, None)
        polled = provider.poll("dirty")
        self.assertEqual(len(polled), 1)  # newest payload supersedes the older ones
        self.assertEqual(polled[0][0], "imports.job")
        self.assertEqual(polled[0][1]["status"], "completed")
        self.assertEqual(provider.poll("dirty"), [])
        self.assertEqual(drain_user_events("imports", self.user.id), [])

    def test_failed_job_records_the_reason(self):
        self.provider.fail_list.add("/")
        job = self._pending()
        self.assertIs(svc.run_job(job.pk), Outcome.FAILED)
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
            self.assertIs(svc.run_job(job.pk), Outcome.FAILED)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertIn("Unexpected error", job.error)

    def test_only_pending_or_running_jobs_run(self):
        job = self._pending()
        job.status = ImportJob.Status.COMPLETED
        job.save()
        self.assertIs(svc.run_job(job.pk), Outcome.SKIPPED)
        self.assertIs(
            svc.run_job("00000000-0000-0000-0000-000000000000"), Outcome.SKIPPED
        )

    def test_a_second_worker_is_turned_away_by_the_lock(self):
        job = self._pending()
        with patch("workspace.imports.services.jobs.task_lock") as lock:
            lock.return_value.__enter__.return_value = False
            self.assertIs(svc.run_job(job.pk), Outcome.SKIPPED)

    def test_paused_slice_keeps_the_job_running_and_its_stats(self):
        job = self._pending()
        with override_settings(IMPORTS_BATCH_SECONDS=0):
            self.assertIs(svc.run_job(job.pk), Outcome.PAUSED)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.RUNNING)
        self.assertEqual(job.stats["files"]["phase"], "listing")
        self.assertIs(svc.run_job(job.pk), Outcome.DONE)

    def test_heartbeat_is_stamped_while_running(self):
        job = self._pending()
        svc.run_job(job.pk)
        job.refresh_from_db()
        self.assertIsNotNone(job.heartbeat_at)

    def test_soft_time_limit_pauses_instead_of_failing(self):
        from celery.exceptions import SoftTimeLimitExceeded

        job = self._pending()
        with patch(
            "workspace.imports.services.jobs._run_slice",
            side_effect=SoftTimeLimitExceeded(),
        ):
            self.assertIs(svc.run_job(job.pk), Outcome.PAUSED)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.RUNNING)

    def test_a_transfer_that_never_fits_a_slice_ends_as_failed_entry(self):
        """Before the attempts guard the runner paused and re-enqueued forever:
        every slice restarted the same download and hit the soft limit again."""
        from celery.exceptions import SoftTimeLimitExceeded

        from .fakes import FakeFileSource

        original_open = FakeFileSource.open

        def cut_open(source_self, entry):
            if entry.id == "/b.txt":
                raise SoftTimeLimitExceeded()
            return original_open(source_self, entry)

        job = self._pending()
        with patch.object(FakeFileSource, "open", cut_open):
            self.assertIs(svc.run_job(job.pk), Outcome.PAUSED)
            job.refresh_from_db()
            self.assertEqual(
                job.stats["files"]["in_flight"], {"id": "/b.txt", "attempts": 1}
            )
            self.assertIs(svc.run_job(job.pk), Outcome.PAUSED)
            self.assertIs(svc.run_job(job.pk), Outcome.DONE)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.COMPLETED)
        self.assertEqual(job.stats["files"]["failed"], 1)
        self.assertEqual(job.stats["files"]["files"], 2)
        self.assertEqual(
            ImportJobItem.objects.get(job=job, remote_id="/b.txt").status,
            ImportJobItem.Status.FAILED,
        )

    @override_settings(IMPORTS_ALLOWED_HOSTS=[])
    def test_every_slice_vets_the_remote_url_again(self):
        """The URL was vetted when the connection was saved, but the worker
        contacts it hours later: a host that now resolves to a forbidden
        address fails the job instead of being fetched."""
        ImportConnection.objects.filter(pk=self.conn.pk).update(
            base_url="http://127.0.0.1/dav"
        )
        job = self._pending()
        self.assertIs(svc.run_job(job.pk), Outcome.FAILED)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertIn("will not contact", job.error)

    def test_cancel_requested_ends_as_cancelled(self):
        job = self._pending()
        ImportJob.objects.filter(pk=job.pk).update(
            status=ImportJob.Status.RUNNING, cancel_requested_at=timezone.now()
        )
        self.assertIs(svc.run_job(job.pk), Outcome.CANCELLED)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.CANCELLED)
        self.assertIn("cancelled", Notification.objects.get().title)


class CancelRetryPurgeTests(JobsTestCase):
    def test_cancel_pending_job_ends_it_immediately(self):
        job = ImportJob.objects.create(connection=self.conn, kinds=["files"])
        job = svc.cancel_job(job)
        self.assertEqual(job.status, ImportJob.Status.CANCELLED)
        self.assertIsNotNone(job.finished_at)
        self.assertIs(svc.run_job(job.pk), Outcome.SKIPPED)

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
    def test_purge_drops_old_error_reports_but_keeps_the_done_memory(self):
        old = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.COMPLETED,
            finished_at=timezone.now() - timedelta(days=31),
        )
        kept = ImportJobItem.objects.create(
            job=old, kind="files", remote_id="/a", status=ImportJobItem.Status.DONE
        )
        ImportJobItem.objects.create(
            job=old, kind="files", remote_id="/b", status=ImportJobItem.Status.FAILED
        )
        ImportJobItem.objects.create(
            job=old, kind="files", remote_id="/c", status=ImportJobItem.Status.SKIPPED
        )
        recent = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.COMPLETED,
            finished_at=timezone.now() - timedelta(days=2),
        )
        ImportJobItem.objects.create(
            job=recent, kind="files", remote_id="/d", status=ImportJobItem.Status.FAILED
        )
        self.assertEqual(purge_old_jobs.apply().result, {"deleted": 2})
        self.assertEqual(ImportJob.objects.count(), 2)
        self.assertEqual(
            set(ImportJobItem.objects.values_list("remote_id", flat=True)), {"/a", "/d"}
        )
        self.assertTrue(ImportJobItem.objects.filter(pk=kept.pk).exists())

    @override_settings(IMPORTS_BATCH_SECONDS=60)
    def test_stale_running_jobs_are_re_enqueued(self):
        stale = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=1),
            heartbeat_at=timezone.now() - timedelta(minutes=30),
        )
        other_conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Other",
            base_url="https://y/dav",
            username="b",
        )
        ImportJob.objects.create(
            connection=other_conn,
            kinds=["files"],
            status=ImportJob.Status.RUNNING,
            started_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )
        with patch("workspace.imports.services.jobs._enqueue") as enqueue:
            self.assertEqual(recover_stale_jobs.apply().result, {"recovered": 1})
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0].pk, stale.pk)
        stale.refresh_from_db()
        self.assertGreater(stale.heartbeat_at, timezone.now() - timedelta(minutes=1))
        # and the next scan leaves it alone
        with patch("workspace.imports.services.jobs._enqueue") as enqueue:
            self.assertEqual(recover_stale_jobs.apply().result, {"recovered": 0})

    @override_settings(IMPORTS_BATCH_SECONDS=60)
    def test_a_recovered_job_resumes_and_finishes(self):
        job = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            options={"files": {}},
            status=ImportJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=1),
            heartbeat_at=timezone.now() - timedelta(hours=1),
        )
        self.assertIs(svc.run_job(job.pk), Outcome.DONE)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.COMPLETED)


class TaskTests(JobsTestCase):
    def test_eager_task_loops_over_paused_slices(self):
        with patch(
            "workspace.imports.services.jobs.run_job",
            side_effect=[Outcome.PAUSED, Outcome.PAUSED, Outcome.DONE],
        ) as run:
            result = run_import_job.apply(
                args=["00000000-0000-0000-0000-000000000000"]
            ).result
        self.assertEqual(result, {"status": "done"})
        self.assertEqual(run.call_count, 3)

    def test_worker_task_re_enqueues_itself_when_paused(self):
        with (
            patch(
                "workspace.imports.services.jobs.run_job", return_value=Outcome.PAUSED
            ),
            patch.object(run_import_job, "apply_async") as apply_async,
        ):
            # Bypass .apply() so the request is not flagged eager.
            result = run_import_job.run("00000000-0000-0000-0000-000000000000")
        self.assertEqual(result, {"status": "paused"})
        apply_async.assert_called_once()

    def test_summary_iterates_kinds_through_their_importer(self):
        job = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            stats={"files": {"files": 2, "unchanged": 1, "failed": 1}},
        )
        self.assertEqual(svc.summarize(job), "2 files, 1 unchanged, 1 failed")
        job.stats = {}
        self.assertEqual(svc.summarize(job), "Nothing to import.")


class EagerLoopBoundTests(JobsTestCase):
    def test_eager_loop_gives_up_loudly_when_no_slice_progresses(self):
        with (
            patch(
                "workspace.imports.services.jobs.run_job", return_value=Outcome.PAUSED
            ),
            patch("workspace.imports.tasks._MAX_EAGER_SLICES", 3),
        ):
            with self.assertRaisesRegex(RuntimeError, "paused 3 times"):
                run_import_job.apply(
                    args=["00000000-0000-0000-0000-000000000000"], throw=True
                )

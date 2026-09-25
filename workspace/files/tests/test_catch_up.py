"""The hourly catch-up: one task per pending file of every registered reader."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from workspace.common.task_priority import BACKGROUND_PRIORITY
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services import catch_up as registry
from workspace.files.services.catch_up import (
    pending_ids,
    queue_files,
    queue_pending,
    register_catch_up,
    resolve,
)
from workspace.files.tasks import CATCH_UP_EXPIRY_SHARE, catch_up, catch_up_file

User = get_user_model()


class FakeReader:
    """Reads .txt files; a file is pending until it has been processed.

    A file named bad-* is never processed: it stays pending for good, like a
    blob no reader can make sense of.
    """

    def __init__(self, name="fake"):
        self.name = name
        self.processed = []
        self.attempted = []

    def pending(self, *, reanalyze=False):
        qs = File.objects.filter(
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
            name__endswith=".txt",
        )
        return qs if reanalyze else qs.exclude(pk__in=self.processed)

    def process(self, file_obj):
        self.attempted.append(file_obj.pk)
        if file_obj.name.startswith("bad-"):
            return False
        self.processed.append(file_obj.pk)
        return True


def run_catch_up():
    """Run the hourly pass, then every task it queued; return stats and calls."""
    with patch.object(catch_up_file, "apply_async") as queue:
        stats = catch_up.apply().get()
    for call in queue.call_args_list:
        catch_up_file.apply(args=call.kwargs["args"], kwargs=call.kwargs.get("kwargs"))
    return stats, queue.call_args_list


class CatchUpTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        registered = patch.dict(registry._CATCH_UPS, clear=True)
        registered.start()
        self.addCleanup(registered.stop)
        self.reader = FakeReader()
        register_catch_up(
            "fake", pending=self.reader.pending, process=self.reader.process
        )
        # The rotation cursor lives in the cache, which LocMemCache keeps
        # across tests.
        self.addCleanup(cache.clear)

    def _upload(self, name):
        return FileService.create_file(
            owner=self.user, name=name, content=ContentFile(b"x", name=name)
        )


class CatchUpTaskTests(CatchUpTestCase):
    def test_processes_every_pending_file_and_is_idempotent(self):
        a = self._upload("a.txt")
        b = self._upload("b.txt")
        self._upload("c.md")

        self.assertEqual(run_catch_up()[0], {"fake": 2})
        self.assertCountEqual(self.reader.processed, [a.pk, b.pk])
        self.assertEqual(run_catch_up()[0], {"fake": 0})

    def test_queues_one_low_priority_task_per_file_expiring_before_the_next_pass(
        self,
    ):
        """Whatever the workers did not reach is dropped before the next pass
        queues the file again, so a file is never attempted twice per pass."""
        f = self._upload("a.txt")
        started = timezone.now()

        _, calls = run_catch_up()

        self.assertEqual([c.kwargs["args"] for c in calls], [["fake", str(f.pk)]])
        self.assertEqual(calls[0].kwargs["priority"], BACKGROUND_PRIORITY)
        interval = timedelta(seconds=settings.FILES_CATCH_UP_INTERVAL)
        expires = calls[0].kwargs["expires"]
        self.assertGreaterEqual(expires, started + interval * CATCH_UP_EXPIRY_SHARE)
        self.assertLess(expires, started + interval)

    def test_every_registered_reader_runs(self):
        other = FakeReader("other")
        register_catch_up("other", pending=other.pending, process=other.process)
        f = self._upload("a.txt")

        self.assertEqual(run_catch_up()[0], {"fake": 1, "other": 1})
        self.assertEqual(self.reader.processed, [f.pk])
        self.assertEqual(other.processed, [f.pk])

    def test_names_limit_the_pass_to_those_readers(self):
        other = FakeReader("other")
        register_catch_up("other", pending=other.pending, process=other.process)
        self._upload("a.txt")

        with patch.object(catch_up_file, "apply_async") as queue:
            stats = catch_up.apply(kwargs={"names": ["other", "gone"]}).get()

        self.assertEqual(stats, {"other": 1})
        self.assertEqual([c.kwargs["args"][0] for c in queue.call_args_list], ["other"])

    @patch("workspace.files.tasks.CATCH_UP_LIMIT", 2)
    def test_one_pass_is_bounded(self):
        """A backlog larger than one pass drains over the following ones."""
        for name in ("a.txt", "b.txt", "c.txt"):
            self._upload(name)

        self.assertEqual(run_catch_up()[0], {"fake": 2})
        self.assertEqual(run_catch_up()[0], {"fake": 1})
        self.assertEqual(len(self.reader.processed), 3)

    @patch("workspace.files.tasks.CATCH_UP_LIMIT", 20)
    def test_files_that_keep_failing_do_not_starve_the_ones_behind_them(self):
        """More failing files than a pass takes: without a rotating start,
        every pass would queue the same 20 oldest files and never the rest."""
        for i in range(25):
            self._upload(f"bad-{i:02}.txt")
        healthy = self._upload("good.txt")

        self.assertEqual(run_catch_up()[0], {"fake": 20})
        self.assertEqual(self.reader.processed, [])

        self.assertEqual(run_catch_up()[0], {"fake": 20})
        self.assertEqual(self.reader.processed, [healthy.pk])
        # Every failing file has come up once over the two passes.
        self.assertEqual(len(set(self.reader.attempted)), 26)

    def test_a_resumed_pass_wraps_around_to_the_start(self):
        uploaded = [self._upload(f"{i}.txt").pk for i in range(4)]
        reader = registry.get_catch_up("fake")

        ids = list(pending_ids(reader, start_after=uploaded[1]))

        self.assertEqual(ids, [*uploaded[2:], *uploaded[:2]])

    def test_pending_ids_pages_through_everything(self):
        uploaded = {self._upload(f"{i}.txt").pk for i in range(5)}

        with patch.object(registry, "_PAGE_SIZE", 2):
            self.assertEqual(set(pending_ids(registry.get_catch_up("fake"))), uploaded)


class RegistryTests(CatchUpTestCase):
    def test_resolve_takes_every_reader_by_default(self):
        other = FakeReader("other")
        register_catch_up("other", pending=other.pending, process=other.process)

        readers, unknown = resolve(None)

        self.assertEqual([r.name for r in readers], ["fake", "other"])
        self.assertEqual(unknown, [])

    def test_resolve_keeps_the_order_asked_and_reports_unknown_names(self):
        other = FakeReader("other")
        register_catch_up("other", pending=other.pending, process=other.process)

        readers, unknown = resolve(["other", "gone", "fake", "other"])

        self.assertEqual([r.name for r in readers], ["other", "fake"])
        self.assertEqual(unknown, ["gone"])

    def test_queue_pending_queues_one_low_priority_task_per_file(self):
        a = self._upload("a.txt")

        with patch.object(catch_up_file, "apply_async") as queue:
            queued = queue_pending(
                registry.get_catch_up("fake"), reanalyze=True, expires=60
            )

        self.assertEqual(queued, 1)
        queue.assert_called_once_with(
            args=["fake", str(a.pk)],
            kwargs={"reanalyze": True},
            priority=BACKGROUND_PRIORITY,
            expires=60,
        )

    def test_queue_files_queues_each_file_asked_for(self):
        a = self._upload("a.txt")
        self.reader.processed.append(a.pk)

        with patch.object(catch_up_file, "apply_async") as queue:
            queued = queue_files(registry.get_catch_up("fake"), [a.pk])

        self.assertEqual(queued, 1)
        queue.assert_called_once_with(
            args=["fake", str(a.pk)],
            kwargs={"reanalyze": False},
            priority=BACKGROUND_PRIORITY,
            expires=None,
        )

    def test_a_disabled_reader_has_nothing_pending(self):
        register_catch_up(
            "off",
            pending=self.reader.pending,
            process=self.reader.process,
            enabled=lambda: False,
        )
        f = self._upload("a.txt")

        self.assertEqual(run_catch_up()[0], {"fake": 1, "off": 0})
        self.assertEqual(
            catch_up_file.apply(args=["off", str(f.pk)]).get(), {"status": "skipped"}
        )
        self.assertEqual(self.reader.processed, [f.pk])


class CatchUpFileTaskTests(CatchUpTestCase):
    def test_processes_a_pending_file(self):
        f = self._upload("a.txt")

        status = catch_up_file.apply(args=["fake", str(f.pk)]).get()

        self.assertEqual(status, {"status": "ok"})
        self.assertEqual(self.reader.processed, [f.pk])

    def test_a_file_processed_since_it_was_queued_is_not_read_again(self):
        """Its upload event may have run while the task waited in the queue."""
        f = self._upload("a.txt")
        self.reader.processed.append(f.pk)

        status = catch_up_file.apply(args=["fake", str(f.pk)]).get()

        self.assertEqual(status, {"status": "skipped"})
        self.assertEqual(self.reader.processed, [f.pk])

    def test_reanalyze_processes_an_up_to_date_file(self):
        f = self._upload("a.txt")
        self.reader.processed.append(f.pk)

        status = catch_up_file.apply(
            args=["fake", str(f.pk)], kwargs={"reanalyze": True}
        ).get()

        self.assertEqual(status, {"status": "ok"})
        self.assertEqual(self.reader.processed, [f.pk, f.pk])

    def test_nothing_written_is_skipped(self):
        register_catch_up("noop", pending=self.reader.pending, process=lambda f: False)
        f = self._upload("a.txt")

        status = catch_up_file.apply(args=["noop", str(f.pk)]).get()

        self.assertEqual(status, {"status": "skipped"})

    def test_unknown_reader_or_file(self):
        f = self._upload("a.txt")
        cases = [
            ("gone", str(f.pk)),
            ("fake", "00000000-0000-0000-0000-000000000000"),
            ("fake", "nope"),
            ("fake", None),
        ]
        for name, file_uuid in cases:
            with self.subTest(name=name, file_uuid=file_uuid):
                self.assertEqual(
                    catch_up_file.apply(args=[name, file_uuid]).get(),
                    {"status": "skipped"},
                )
        self.assertEqual(self.reader.processed, [])


class CatchUpCommandTests(CatchUpTestCase):
    def setUp(self):
        super().setUp()
        self.a = self._upload("a.txt")
        self.b = self._upload("b.txt")

    def _run(self, *args):
        out = StringIO()
        call_command("catch_up", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_counts_and_processes_nothing(self):
        with patch.object(catch_up_file, "apply_async") as queue:
            output = self._run("--dry-run")

        self.assertIn("fake: would process 2 file(s).", output)
        self.assertIn(
            "fake: would process 1 file(s).", self._run("--dry-run", "--limit", "1")
        )
        queue.assert_not_called()
        self.assertEqual(self.reader.processed, [])

    def test_queues_one_low_priority_task_per_file(self):
        with patch.object(catch_up_file, "apply_async") as queue:
            output = self._run()

        self.assertIn("fake: queued 2 file(s).", output)
        self.assertCountEqual(
            [c.kwargs["args"] for c in queue.call_args_list],
            [["fake", str(self.a.pk)], ["fake", str(self.b.pk)]],
        )
        for call in queue.call_args_list:
            self.assertEqual(call.kwargs["kwargs"], {"reanalyze": False})
            self.assertEqual(call.kwargs["priority"], BACKGROUND_PRIORITY)

    def test_sync_is_idempotent(self):
        self.assertIn("fake: processed 2 file(s).", self._run("--sync"))
        self.assertIn("fake: processed 0 file(s).", self._run("--sync"))
        self.assertEqual(len(self.reader.processed), 2)

    def test_reanalyze_processes_up_to_date_files_again(self):
        self._run("--sync")

        self.assertIn("fake: processed 2 file(s).", self._run("--sync", "--reanalyze"))
        self.assertEqual(len(self.reader.processed), 4)

    def test_runs_only_the_named_readers(self):
        other = FakeReader("other")
        register_catch_up("other", pending=other.pending, process=other.process)

        output = self._run("other", "--sync")

        self.assertIn("other: processed 2 file(s).", output)
        self.assertNotIn("fake", output)
        self.assertEqual(self.reader.processed, [])

    def test_unknown_reader(self):
        with self.assertRaisesMessage(CommandError, "Unknown reader(s): gone"):
            self._run("gone")

    def test_a_disabled_reader_is_reported_and_skipped(self):
        register_catch_up(
            "off",
            pending=self.reader.pending,
            process=self.reader.process,
            enabled=lambda: False,
        )

        output = self._run("off", "--sync")

        self.assertIn("off: disabled on this deployment.", output)
        self.assertEqual(self.reader.processed, [])


class CatchUpScheduleTests(SimpleTestCase):
    def test_runs_hourly_and_queued_files_expire_at_the_next_pass(self):
        entry = settings.CELERY_BEAT_SCHEDULE["catch-up-readers"]

        self.assertEqual(entry["task"], "files.catch_up")
        self.assertEqual(entry["schedule"], 3600.0)
        # A tick that never started is dropped: no second pass stacks up.
        self.assertEqual(entry["options"], {"expires": entry["schedule"]})
        self.assertEqual(entry["schedule"], settings.FILES_CATCH_UP_INTERVAL)

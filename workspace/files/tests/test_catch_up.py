"""The hourly catch-up: one task per pending file of every registered reader."""

from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from workspace.common.task_priority import BACKGROUND_PRIORITY
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services import catch_up as registry
from workspace.files.services.catch_up import pending_ids, register_catch_up
from workspace.files.tasks import CATCH_UP_EXPIRES, catch_up, catch_up_file

User = get_user_model()


class FakeReader:
    """Reads .txt files; a file is pending until it has been processed."""

    def __init__(self, name="fake"):
        self.name = name
        self.processed = []

    def pending(self, *, reanalyze=False):
        qs = File.objects.filter(
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
            name__endswith=".txt",
        )
        return qs if reanalyze else qs.exclude(pk__in=self.processed)

    def process(self, file_obj):
        self.processed.append(file_obj.pk)
        return file_obj


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

    def test_queues_one_low_priority_task_per_file_expiring_with_the_pass(self):
        """The backlog never holds up other tasks, and what the workers did not
        reach before the next pass is dropped rather than queued twice."""
        f = self._upload("a.txt")

        _, calls = run_catch_up()

        self.assertEqual([c.kwargs["args"] for c in calls], [["fake", str(f.pk)]])
        self.assertEqual(calls[0].kwargs["priority"], BACKGROUND_PRIORITY)
        self.assertEqual(calls[0].kwargs["expires"], CATCH_UP_EXPIRES)

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

    def test_pending_ids_pages_through_everything(self):
        uploaded = {self._upload(f"{i}.txt").pk for i in range(5)}

        with patch.object(registry, "_PAGE_SIZE", 2):
            self.assertEqual(set(pending_ids(registry.get_catch_up("fake"))), uploaded)


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
        register_catch_up("noop", pending=self.reader.pending, process=lambda f: None)
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


class CatchUpScheduleTests(SimpleTestCase):
    def test_runs_hourly_and_queued_files_expire_at_the_next_pass(self):
        entry = settings.CELERY_BEAT_SCHEDULE["catch-up-readers"]

        self.assertEqual(entry["task"], "files.catch_up")
        self.assertEqual(entry["schedule"], 3600.0)
        # A tick that never started is dropped: no second pass stacks up.
        self.assertEqual(entry["options"], {"expires": entry["schedule"]})
        self.assertEqual(CATCH_UP_EXPIRES, entry["schedule"])

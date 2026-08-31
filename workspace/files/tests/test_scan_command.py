from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File, FileScan

User = get_user_model()
ENABLED = {"FILES_MALWARE_SCAN_ENABLED": True}


class ScanFilesCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cmd", password="p")
        self.unscanned = self._file("new.txt")
        self.scanned = self._file("old.txt")
        # The verdict records the hash of the bytes the file holds, which is
        # what makes it up to date. Omitting it would mean "cannot vouch for
        # these bytes", and the command would rightly queue the file again.
        FileScan.objects.create(
            file=self.scanned,
            status=FileScan.Status.CLEAN,
            content_hash=self.scanned.content_hash,
            scanned_at=timezone.now(),
        )
        self.folder = File.objects.create(
            owner=self.user, name="dir", node_type=File.NodeType.FOLDER
        )

    def _file(self, name):
        """Created through FileService, which is what computes content_hash."""
        from workspace.files.services import FileService

        return FileService.create_file(
            self.user, name, content=ContentFile(b"body", name=name)
        )

    def _run(self, *args, **kwargs):
        out = StringIO()
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            call_command("scan_files", *args, stdout=out, **kwargs)
        return out.getvalue(), delay

    def test_default_enqueues_only_unscanned_files(self):
        _, delay = self._run()
        self.assertEqual(delay.call_count, 1)
        delay.assert_called_once_with(str(self.unscanned.uuid))

    def test_rescan_enqueues_every_file(self):
        _, delay = self._run("--rescan")
        self.assertEqual(delay.call_count, 2)

    def test_folders_are_never_enqueued(self):
        _, delay = self._run("--rescan")
        enqueued = {c.args[0] for c in delay.call_args_list}
        self.assertNotIn(str(self.folder.uuid), enqueued)

    def test_limit_caps_the_batch(self):
        self._file("another.txt")
        _, delay = self._run("--limit", "1")
        self.assertEqual(delay.call_count, 1)

    def test_dry_run_enqueues_nothing_but_reports_the_count(self):
        out, delay = self._run("--dry-run")
        delay.assert_not_called()
        self.assertIn("1", out)

    def test_sync_runs_the_task_inline(self):
        out = StringIO()
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file") as task,
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            task.return_value = {"status": "clean"}
            call_command("scan_files", "--sync", stdout=out)
        delay.assert_not_called()
        task.assert_called_once_with(str(self.unscanned.uuid))

    def test_refuses_to_run_when_scanning_is_disabled(self):
        out = StringIO()
        with (
            override_settings(FILES_MALWARE_SCAN_ENABLED=False),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            call_command("scan_files", stdout=out)
        delay.assert_not_called()
        self.assertIn("disabled", out.getvalue().lower())

    def test_trashed_files_are_skipped(self):
        trashed = self._file("gone.txt")
        trashed.deleted_at = timezone.now()
        trashed.save(update_fields=["deleted_at"])
        _, delay = self._run()
        enqueued = {c.args[0] for c in delay.call_args_list}
        self.assertNotIn(str(trashed.uuid), enqueued)


class ScanFilesNullContentTests(TestCase):
    """A NULL content row survives exclude(content=""), so it needs its own guard."""

    def setUp(self):
        self.user = User.objects.create_user(username="nullc", password="p")
        self.real = File(owner=self.user, name="real.txt", node_type=File.NodeType.FILE)
        self.real.content = ContentFile(b"body", name="real.txt")
        self.real.size = 4
        self.real.save()
        self.null = File.objects.create(
            owner=self.user, name="null.txt", node_type=File.NodeType.FILE
        )
        File.objects.filter(pk=self.null.pk).update(content=None)

    def test_a_null_content_row_is_not_enqueued(self):
        out = StringIO()
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            call_command("scan_files", stdout=out)
        enqueued = {c.args[0] for c in delay.call_args_list}
        self.assertIn(str(self.real.uuid), enqueued)
        self.assertNotIn(str(self.null.uuid), enqueued)

    def test_a_null_content_row_does_not_consume_the_limit(self):
        out = StringIO()
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            call_command("scan_files", "--limit", "1", stdout=out)
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(delay.call_args_list[0].args[0], str(self.real.uuid))


class ScanFilesStaleVerdictTests(TestCase):
    """The backfill picks up a file whose content changed since its verdict.

    Without this, a lost CONTENT_REPLACED event strands the file with a
    verdict about bytes it no longer holds, and only --rescan (which re-reads
    the whole library) would ever correct it.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="stale", password="p")

    def _scanned_file(self, name, body, *, verdict_hash=None):
        from workspace.files.services import FileService

        f = FileService.create_file(
            self.user, name, content=ContentFile(body, name=name)
        )
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.CLEAN,
            content_hash=f.content_hash if verdict_hash is None else verdict_hash,
            scanned_at=timezone.now(),
        )
        return f

    def _queued(self):
        out = StringIO()
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            call_command("scan_files", stdout=out)
        return {c.args[0] for c in delay.call_args_list}

    def test_a_file_whose_verdict_matches_its_bytes_is_skipped(self):
        f = self._scanned_file("fresh.txt", b"body")
        self.assertNotIn(str(f.uuid), self._queued())

    def test_a_file_whose_content_changed_is_picked_up(self):
        from workspace.files.services import FileService

        f = self._scanned_file("changed.txt", b"body")
        FileService.update_content(f, ContentFile(b"different", name="changed.txt"))
        self.assertIn(str(f.uuid), self._queued())

    def test_a_verdict_with_no_recorded_hash_is_picked_up(self):
        """Rows written before the field existed must not look up to date."""
        f = self._scanned_file("legacy.txt", b"body", verdict_hash="")
        self.assertIn(str(f.uuid), self._queued())

    def test_a_file_whose_own_hash_is_missing_is_picked_up(self):
        """An empty File.content_hash means we cannot vouch for the bytes."""
        f = self._scanned_file("nohash.txt", b"body")
        File.objects.filter(pk=f.pk).update(content_hash="")
        self.assertIn(str(f.uuid), self._queued())

    def test_a_never_scanned_file_is_still_picked_up(self):
        from workspace.files.services import FileService

        f = FileService.create_file(
            self.user, "new.txt", content=ContentFile(b"body", name="new.txt")
        )
        self.assertIn(str(f.uuid), self._queued())

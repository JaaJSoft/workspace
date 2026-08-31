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
        FileScan.objects.create(
            file=self.scanned,
            status=FileScan.Status.CLEAN,
            scanned_at=timezone.now(),
        )
        self.folder = File.objects.create(
            owner=self.user, name="dir", node_type=File.NodeType.FOLDER
        )

    def _file(self, name):
        f = File(owner=self.user, name=name, node_type=File.NodeType.FILE)
        f.content = ContentFile(b"body", name=name)
        f.size = 4
        f.save()
        return f

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

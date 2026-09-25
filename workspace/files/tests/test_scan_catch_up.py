"""The malware scan's reader, run by the hourly catch-up (files.catch_up)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File, FileScan
from workspace.files.services import FileService
from workspace.files.services.catch_up import get_catch_up
from workspace.files.services.scanning.base import ScanVerdict
from workspace.files.services.scanning.scan import pending_scan_qs, scan_for_catch_up

from .catch_up import run_catch_up

User = get_user_model()


class _CleanScanner:
    def scan(self, stream, *, name=""):
        stream.read()
        return ScanVerdict(status=FileScan.Status.CLEAN)


@override_settings(FILES_MALWARE_SCAN_ENABLED=True)
class PendingScanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cmd", password="p")

    def _file(self, name, body=b"body"):
        """Created through FileService, which is what computes content_hash."""
        return FileService.create_file(
            self.user, name, content=ContentFile(body, name=name)
        )

    def _scanned_file(self, name, *, verdict_hash=None):
        f = self._file(name)
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.CLEAN,
            content_hash=f.content_hash if verdict_hash is None else verdict_hash,
            scanned_at=timezone.now(),
        )
        return f

    def _pending(self, **kwargs):
        return set(pending_scan_qs(**kwargs).values_list("pk", flat=True))

    def test_registered_with_the_catch_up(self):
        self.assertIs(get_catch_up("malware_scan").process, scan_for_catch_up)

    def test_a_never_scanned_file_is_pending(self):
        f = self._file("new.txt")
        self.assertEqual(self._pending(), {f.pk})

    def test_a_file_whose_verdict_matches_its_bytes_is_not_pending(self):
        self._scanned_file("fresh.txt")
        self.assertEqual(self._pending(), set())

    def test_reanalyze_takes_every_file(self):
        f = self._scanned_file("fresh.txt")
        self.assertEqual(self._pending(reanalyze=True), {f.pk})

    def test_a_file_whose_content_changed_is_pending(self):
        """Without this, a lost CONTENT_REPLACED event strands the file with a
        verdict about bytes it no longer holds."""
        f = self._scanned_file("changed.txt")
        FileService.update_content(f, ContentFile(b"different", name="changed.txt"))
        self.assertEqual(self._pending(), {f.pk})

    def test_a_verdict_with_no_recorded_hash_is_pending(self):
        """Rows written before the field existed must not look up to date."""
        f = self._scanned_file("legacy.txt", verdict_hash="")
        self.assertEqual(self._pending(), {f.pk})

    def test_a_file_whose_own_hash_is_missing_is_pending(self):
        """An empty File.content_hash means we cannot vouch for the bytes."""
        f = self._scanned_file("nohash.txt")
        File.objects.filter(pk=f.pk).update(content_hash="")
        self.assertEqual(self._pending(), {f.pk})

    def test_folders_trashed_files_and_null_contents_are_never_pending(self):
        File.objects.create(owner=self.user, name="dir", node_type=File.NodeType.FOLDER)
        trashed = self._file("gone.txt")
        File.objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
        # A NULL content row survives exclude(content=""), so it needs its own
        # exclusion.
        null = File.objects.create(
            owner=self.user, name="null.txt", node_type=File.NodeType.FILE
        )
        File.objects.filter(pk=null.pk).update(content=None)

        self.assertEqual(self._pending(reanalyze=True), set())

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_nothing_is_pending_when_scanning_is_disabled(self):
        self._file("new.txt")
        reader = get_catch_up("malware_scan")

        self.assertFalse(reader.enabled())
        self.assertFalse(reader.pending_files().exists())
        self.assertFalse(reader.pending_files(reanalyze=True).exists())

    def test_no_scanner_writes_no_verdict(self):
        f = self._file("new.txt")
        with patch(
            "workspace.files.services.scanning.registry.get_scanner",
            return_value=None,
        ):
            self.assertFalse(scan_for_catch_up(f))
        self.assertFalse(FileScan.objects.exists())

    def test_the_catch_up_writes_the_missing_verdicts(self):
        f = self._file("new.txt")
        self._scanned_file("fresh.txt")

        with patch(
            "workspace.files.services.scanning.registry.get_scanner",
            return_value=_CleanScanner(),
        ):
            self.assertEqual(run_catch_up("malware_scan"), 1)

        scan = FileScan.objects.get(file=f)
        self.assertEqual(scan.status, FileScan.Status.CLEAN)
        self.assertEqual(scan.content_hash, f.content_hash)
        self.assertEqual(self._pending(), set())

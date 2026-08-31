from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File, FileScan
from workspace.files.services.scanning.base import ScanVerdict
from workspace.files.tasks import scan_file

User = get_user_model()
ENABLED = {"FILES_MALWARE_SCAN_ENABLED": True}


class _StubScanner:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    def scan(self, stream, *, name=""):
        self.calls.append(stream.read())
        return self.verdict

    def health(self):
        raise AssertionError("not used here")


class _RacingScanner:
    """Replaces the row's content while its own scan is still in flight.

    Stands in for the real ordering: a slow scan of a large infected upload
    returning after the user has already replaced the file and after the
    second scan wrote its verdict.
    """

    def __init__(self, verdict, file_obj, new_hash):
        self.verdict = verdict
        self.file_obj = file_obj
        self.new_hash = new_hash

    def scan(self, stream, *, name=""):
        stream.read()
        File.objects.filter(pk=self.file_obj.pk).update(content_hash=self.new_hash)
        return self.verdict

    def health(self):
        raise AssertionError("not used here")


class ScanTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="task", password="p")

    def _file(self, name="a.txt", body=b"hello", size=None):
        f = File(owner=self.user, name=name, node_type=File.NodeType.FILE)
        f.content = ContentFile(body, name=name)
        f.size = len(body) if size is None else size
        f.save()
        return f

    def _run(self, file_obj, verdict):
        scanner = _StubScanner(verdict)
        with (
            override_settings(**ENABLED),
            patch(
                "workspace.files.services.scanning.registry.get_scanner",
                return_value=scanner,
            ),
        ):
            result = scan_file(str(file_obj.uuid))
        return result, scanner

    def test_clean_verdict_is_persisted(self):
        f = self._file()
        result, scanner = self._run(f, ScanVerdict(status=FileScan.Status.CLEAN))
        self.assertEqual(result["status"], FileScan.Status.CLEAN)
        self.assertEqual(f.scan.status, FileScan.Status.CLEAN)
        self.assertIsNotNone(f.scan.scanned_at)
        self.assertEqual(scanner.calls, [b"hello"])

    def test_infected_verdict_records_the_signature(self):
        f = self._file()
        self._run(
            f,
            ScanVerdict(status=FileScan.Status.INFECTED, signature="Unit.Test"),
        )
        self.assertEqual(f.scan.status, FileScan.Status.INFECTED)
        self.assertEqual(f.scan.signature, "Unit.Test")

    def test_rescan_overwrites_the_previous_verdict(self):
        f = self._file()
        self._run(f, ScanVerdict(status=FileScan.Status.INFECTED, signature="Old"))
        self._run(f, ScanVerdict(status=FileScan.Status.CLEAN))
        self.assertEqual(FileScan.objects.filter(file=f).count(), 1)
        f.refresh_from_db()
        self.assertEqual(f.scan.status, FileScan.Status.CLEAN)

    def test_oversize_file_is_skipped_without_contacting_the_scanner(self):
        f = self._file(body=b"x" * 50)
        scanner = _StubScanner(ScanVerdict(status=FileScan.Status.CLEAN))
        with (
            override_settings(**ENABLED, FILES_MALWARE_SCAN_MAX_BYTES=10),
            patch(
                "workspace.files.services.scanning.registry.get_scanner",
                return_value=scanner,
            ),
        ):
            scan_file(str(f.uuid))
        self.assertEqual(f.scan.status, FileScan.Status.SKIPPED)
        self.assertEqual(scanner.calls, [])

    def test_truncated_clean_scan_becomes_skipped(self):
        """A stale size column let an oversize file through; the reader caught
        it, so a clean answer about the first N bytes is not a clean file."""
        f = self._file(body=b"x" * 50, size=5)
        scanner = _StubScanner(ScanVerdict(status=FileScan.Status.CLEAN))
        with (
            override_settings(**ENABLED, FILES_MALWARE_SCAN_MAX_BYTES=10),
            patch(
                "workspace.files.services.scanning.registry.get_scanner",
                return_value=scanner,
            ),
        ):
            scan_file(str(f.uuid))
        self.assertEqual(f.scan.status, FileScan.Status.SKIPPED)

    def test_truncated_infected_scan_stays_infected(self):
        f = self._file(body=b"x" * 50, size=5)
        scanner = _StubScanner(
            ScanVerdict(status=FileScan.Status.INFECTED, signature="Unit.Test")
        )
        with (
            override_settings(**ENABLED, FILES_MALWARE_SCAN_MAX_BYTES=10),
            patch(
                "workspace.files.services.scanning.registry.get_scanner",
                return_value=scanner,
            ),
        ):
            scan_file(str(f.uuid))
        self.assertEqual(f.scan.status, FileScan.Status.INFECTED)

    def test_folder_is_skipped(self):
        folder = File.objects.create(
            owner=self.user, name="d", node_type=File.NodeType.FOLDER
        )
        result, scanner = self._run(folder, ScanVerdict(status=FileScan.Status.CLEAN))
        self.assertEqual(result["status"], "not_applicable")
        self.assertFalse(FileScan.objects.filter(file=folder).exists())

    def test_missing_blob_is_an_error_verdict(self):
        f = self._file()
        f.content.storage.delete(f.content.name)
        self._run(f, ScanVerdict(status=FileScan.Status.CLEAN))
        self.assertEqual(f.scan.status, FileScan.Status.ERROR)

    def test_unknown_uuid_is_not_found(self):
        with override_settings(**ENABLED):
            self.assertEqual(scan_file("not-a-uuid")["status"], "not_found")

    def test_disabled_scanning_writes_nothing(self):
        f = self._file()
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            result = scan_file(str(f.uuid))
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(FileScan.objects.filter(file=f).exists())

    def test_blocking_verdict_drops_the_search_document(self):
        f = self._file()
        with patch("workspace.files.services.search_index.unindex_file") as unindex:
            self._run(
                f, ScanVerdict(status=FileScan.Status.INFECTED, signature="Unit.Test")
            )
        unindex.assert_called_once()

    def test_clean_verdict_reindexes(self):
        f = self._file()
        with patch("workspace.files.services.search_index.index_file") as index:
            self._run(f, ScanVerdict(status=FileScan.Status.CLEAN))
        index.assert_called_once()

    def _run_racing(self, file_obj, verdict, new_hash):
        scanner = _RacingScanner(verdict, file_obj, new_hash)
        with (
            override_settings(**ENABLED),
            patch(
                "workspace.files.services.scanning.registry.get_scanner",
                return_value=scanner,
            ),
        ):
            return scan_file(str(file_obj.uuid))

    def test_a_verdict_about_replaced_content_is_discarded(self):
        f = self._file()
        f.content_hash = "v1"
        f.save(update_fields=["content_hash"])
        result = self._run_racing(
            f,
            ScanVerdict(status=FileScan.Status.INFECTED, signature="Unit.Test"),
            "v2",
        )
        self.assertEqual(result["status"], "stale")
        self.assertFalse(FileScan.objects.filter(file=f).exists())

    def test_a_late_verdict_does_not_overwrite_the_newer_one(self):
        """The scan of v1 outlives the scan of v2 and returns last.

        Without the hash guard it quarantines content it never read, and the
        quarantine is permanent: max_retries=0, and scan_files skips a file
        that already has a row unless it is run with --rescan.
        """
        f = self._file()
        f.content_hash = "v1"
        f.save(update_fields=["content_hash"])
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.CLEAN,
            scanned_at="2026-08-30T12:00:00Z",
        )
        self._run_racing(
            f,
            ScanVerdict(status=FileScan.Status.INFECTED, signature="Unit.Test"),
            "v2",
        )
        f.refresh_from_db()
        self.assertEqual(f.scan.status, FileScan.Status.CLEAN)
        self.assertEqual(f.scan.signature, "")

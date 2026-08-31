from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.files.models import File, FileScan
from workspace.files.services.scanning.policy import (
    blocked_reason,
    blocked_statuses,
    exclude_blocked,
    is_blocked,
    scan_enabled,
    with_scan,
)
from workspace.files.services.scanning.registry import get_scanner

User = get_user_model()


class FileScanModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="scan", password="p")
        self.file = File.objects.create(
            owner=self.user, name="a.txt", node_type=File.NodeType.FILE
        )

    def test_scan_row_is_reachable_as_file_dot_scan(self):
        FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Test.Signature",
            scanned_at="2026-08-30T12:00:00Z",
        )
        self.file.refresh_from_db()
        self.assertEqual(self.file.scan.status, FileScan.Status.INFECTED)
        self.assertEqual(self.file.scan.signature, "Test.Signature")

    def test_file_without_a_row_raises_does_not_exist(self):
        with self.assertRaises(FileScan.DoesNotExist):
            _ = self.file.scan

    def test_one_row_per_file(self):
        from django.db.utils import IntegrityError

        FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.CLEAN,
            scanned_at="2026-08-30T12:00:00Z",
        )
        with self.assertRaises(IntegrityError):
            FileScan.objects.create(
                file=self.file,
                status=FileScan.Status.CLEAN,
                scanned_at="2026-08-30T12:00:00Z",
            )


ENABLED = {"FILES_MALWARE_SCAN_ENABLED": True}


class BlockedStatusesTests(TestCase):
    def test_disabled_blocks_nothing(self):
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            self.assertEqual(blocked_statuses(), frozenset())
            self.assertFalse(scan_enabled())

    def test_block_policy_blocks_infected_only(self):
        with override_settings(
            **ENABLED,
            FILES_MALWARE_ON_DETECTION="block",
            FILES_MALWARE_ON_ERROR="open",
        ):
            self.assertEqual(blocked_statuses(), frozenset({FileScan.Status.INFECTED}))

    def test_flag_policy_blocks_nothing(self):
        with override_settings(
            **ENABLED,
            FILES_MALWARE_ON_DETECTION="flag",
            FILES_MALWARE_ON_ERROR="open",
        ):
            self.assertEqual(blocked_statuses(), frozenset())

    def test_fail_closed_also_blocks_errors(self):
        with override_settings(
            **ENABLED,
            FILES_MALWARE_ON_DETECTION="block",
            FILES_MALWARE_ON_ERROR="closed",
        ):
            self.assertEqual(
                blocked_statuses(),
                frozenset({FileScan.Status.INFECTED, FileScan.Status.ERROR}),
            )

    def test_skipped_is_never_blocked(self):
        with override_settings(
            **ENABLED,
            FILES_MALWARE_ON_DETECTION="block",
            FILES_MALWARE_ON_ERROR="closed",
        ):
            self.assertNotIn(FileScan.Status.SKIPPED, blocked_statuses())


class IsBlockedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pol", password="p")

    def _file(self, name, status=None):
        f = File.objects.create(
            owner=self.user, name=name, node_type=File.NodeType.FILE
        )
        if status is not None:
            FileScan.objects.create(
                file=f, status=status, scanned_at="2026-08-30T12:00:00Z"
            )
        return f

    def test_unscanned_file_is_not_blocked(self):
        f = self._file("a.txt")
        with override_settings(**ENABLED, FILES_MALWARE_ON_DETECTION="block"):
            self.assertFalse(is_blocked(f))
            self.assertIsNone(blocked_reason(f))

    def test_infected_file_is_blocked_under_block_policy(self):
        f = self._file("b.txt", FileScan.Status.INFECTED)
        f.scan.signature = "Unit.Test.Signature"
        f.scan.save()
        with override_settings(**ENABLED, FILES_MALWARE_ON_DETECTION="block"):
            self.assertTrue(is_blocked(f))
            self.assertEqual(blocked_reason(f), "Unit.Test.Signature")

    def test_infected_file_is_readable_under_flag_policy(self):
        f = self._file("c.txt", FileScan.Status.INFECTED)
        with override_settings(**ENABLED, FILES_MALWARE_ON_DETECTION="flag"):
            self.assertFalse(is_blocked(f))

    def test_errored_file_follows_the_error_policy(self):
        f = self._file("d.txt", FileScan.Status.ERROR)
        with override_settings(**ENABLED, FILES_MALWARE_ON_ERROR="open"):
            self.assertFalse(is_blocked(f))
        with override_settings(**ENABLED, FILES_MALWARE_ON_ERROR="closed"):
            self.assertTrue(is_blocked(f))
            self.assertIn("scan", blocked_reason(f).lower())

    def test_nothing_is_blocked_when_scanning_is_disabled(self):
        f = self._file("e.txt", FileScan.Status.INFECTED)
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            self.assertFalse(is_blocked(f))

    def test_model_helper_mirrors_the_policy(self):
        f = self._file("m.txt", FileScan.Status.INFECTED)
        with override_settings(**ENABLED, FILES_MALWARE_ON_DETECTION="block"):
            self.assertTrue(f.is_quarantined())
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            self.assertFalse(f.is_quarantined())


class ExcludeBlockedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="exc", password="p")
        self.clean = File.objects.create(
            owner=self.user, name="clean.txt", node_type=File.NodeType.FILE
        )
        self.unscanned = File.objects.create(
            owner=self.user, name="unscanned.txt", node_type=File.NodeType.FILE
        )
        self.infected = File.objects.create(
            owner=self.user, name="bad.txt", node_type=File.NodeType.FILE
        )
        FileScan.objects.create(
            file=self.clean,
            status=FileScan.Status.CLEAN,
            scanned_at="2026-08-30T12:00:00Z",
        )
        FileScan.objects.create(
            file=self.infected,
            status=FileScan.Status.INFECTED,
            scanned_at="2026-08-30T12:00:00Z",
        )

    def test_unscanned_files_survive_the_exclusion(self):
        """The NOT IN / NULL trap: a library that is mostly unscanned must not
        come back empty."""
        with override_settings(**ENABLED, FILES_MALWARE_ON_DETECTION="block"):
            names = set(
                exclude_blocked(File.objects.all()).values_list("name", flat=True)
            )
        self.assertEqual(names, {"clean.txt", "unscanned.txt"})

    def test_exclusion_is_a_no_op_when_nothing_is_blocked(self):
        with override_settings(**ENABLED, FILES_MALWARE_ON_DETECTION="flag"):
            self.assertEqual(exclude_blocked(File.objects.all()).count(), 3)

    def test_exclusion_returns_the_very_same_queryset_when_disabled(self):
        """Identity, not a query count: ``status__in=frozenset()`` raises
        EmptyResultSet, so an exclude() that was built anyway would collapse to
        the same single query and hide a missing short-circuit."""
        original = File.objects.all()
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            self.assertIs(exclude_blocked(original), original)
            with self.assertNumQueries(1):
                self.assertEqual(original.count(), 3)


class WithScanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wsc", password="p")
        for i in range(3):
            f = File.objects.create(
                owner=self.user, name=f"f{i}.txt", node_type=File.NodeType.FILE
            )
            FileScan.objects.create(
                file=f,
                status=FileScan.Status.CLEAN,
                scanned_at="2026-08-30T12:00:00Z",
            )

    def test_enabled_joins_the_scan_row(self):
        with override_settings(**ENABLED):
            with self.assertNumQueries(1):
                for f in with_scan(File.objects.all()):
                    self.assertEqual(f.scan.status, FileScan.Status.CLEAN)

    def test_disabled_does_not_join(self):
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            qs = with_scan(File.objects.all())
            self.assertNotIn("scan", qs.query.select_related or {})


class RegistryTests(TestCase):
    def test_disabled_yields_no_scanner(self):
        with override_settings(FILES_MALWARE_SCAN_ENABLED=False):
            self.assertIsNone(get_scanner())

    def test_clamav_is_the_default_backend(self):
        from workspace.files.services.scanning.clamav import ClamAVScanner

        with override_settings(**ENABLED, FILES_MALWARE_SCANNER="clamav"):
            self.assertIsInstance(get_scanner(), ClamAVScanner)

    def test_unknown_backend_yields_no_scanner(self):
        with override_settings(**ENABLED, FILES_MALWARE_SCANNER="nope"):
            self.assertIsNone(get_scanner())

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import File, FileScan

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

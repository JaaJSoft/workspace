from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from workspace.files.models import File

User = get_user_model()


class BackfillFileTypesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_backfill_file_with_content(self):
        """Files with content get detected via Magika."""
        f = File.objects.create(
            owner=self.user,
            name="hello.py",
            node_type=File.NodeType.FILE,
            mime_type="text/x-python",
        )
        f.content.save("hello.py", ContentFile(b'print("hello")'))
        f.save()

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        f.refresh_from_db()
        self.assertIsNotNone(f.type)
        self.assertNotEqual(f.type, "")

    def test_backfill_file_without_content(self):
        """Files without content fall back to extension-based detection."""
        File.objects.create(
            owner=self.user,
            name="photo.jpg",
            node_type=File.NodeType.FILE,
            mime_type="image/jpeg",
        )

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        f = File.objects.get(name="photo.jpg")
        self.assertEqual(f.type, "jpeg")

    def test_skips_folders(self):
        """Folders should not get a type - they keep the default 'unknown'."""
        folder = File.objects.create(
            owner=self.user,
            name="docs",
            node_type=File.NodeType.FOLDER,
        )

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        folder.refresh_from_db()
        self.assertEqual(folder.type, "unknown")

    def test_skips_already_labeled(self):
        """Files that already have a type are skipped."""
        f = File.objects.create(
            owner=self.user,
            name="test.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            type="txt",
        )

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        f.refresh_from_db()
        self.assertEqual(f.type, "txt")

    def test_dry_run_does_not_update(self):
        """Dry run reports what would happen but leaves the DB unchanged."""
        File.objects.create(
            owner=self.user,
            name="readme.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )

        out = StringIO()
        call_command("backfill_file_types", "--dry-run", stdout=out)

        f = File.objects.get(name="readme.md")
        self.assertEqual(f.type, "unknown")
        self.assertIn("Would update", out.getvalue())

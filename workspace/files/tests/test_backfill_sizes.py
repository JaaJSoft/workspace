from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from workspace.files.models import File

User = get_user_model()


class BackfillFileSizesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def _create_file(self, name, content, size):
        f = File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=size,
        )
        f.content.save(name, ContentFile(content), save=False)
        # Persist the storage path AND force the size under test in one go
        # (a queryset update bypasses model save logic that could touch size).
        File.objects.filter(pk=f.pk).update(size=size, content=f.content.name)
        f.refresh_from_db()
        return f

    def test_backfills_null_size_from_storage(self):
        f = self._create_file("null-size.txt", b"hello world", None)

        out = StringIO()
        call_command("backfill_file_sizes", stdout=out)

        f.refresh_from_db()
        self.assertEqual(f.size, len(b"hello world"))

    def test_backfills_zero_size_with_real_content(self):
        """Regression test: ZIP extraction used to persist size=0 for entries
        whose blob has real bytes (UploadedFile.size stays at its constructor
        value). The backfill command must repair those rows, not only NULLs."""
        f = self._create_file("zero-size.txt", b"hello world", 0)

        out = StringIO()
        call_command("backfill_file_sizes", stdout=out)

        f.refresh_from_db()
        self.assertEqual(f.size, len(b"hello world"))

    def test_leaves_correct_sizes_untouched(self):
        f = self._create_file("good.txt", b"hello", 5)

        out = StringIO()
        call_command("backfill_file_sizes", stdout=out)

        f.refresh_from_db()
        self.assertEqual(f.size, 5)
        self.assertIn("Updated: 0", out.getvalue())

    def test_batches_updates(self):
        """3 rows with --batch-size 2 exercise both the in-loop flush (full
        batch of 2) and the post-loop flush of the remainder (1)."""
        files = [self._create_file(f"f{i}.txt", b"x" * (i + 1), 0) for i in range(3)]

        out = StringIO()
        call_command("backfill_file_sizes", "--batch-size", "2", stdout=out)

        for i, f in enumerate(files):
            f.refresh_from_db()
            self.assertEqual(f.size, i + 1)
        self.assertIn("Updated: 3", out.getvalue())

    def test_dry_run_does_not_update(self):
        f = self._create_file("dry.txt", b"hello world", None)

        out = StringIO()
        call_command("backfill_file_sizes", "--dry-run", stdout=out)

        f.refresh_from_db()
        self.assertIsNone(f.size)

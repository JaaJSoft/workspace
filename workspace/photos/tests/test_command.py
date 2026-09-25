from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import analyze_media
from workspace.photos.tasks import ANALYSIS_PRIORITY

from .images import jpeg_bytes, png_bytes, upload

User = get_user_model()


def _run(*args):
    out = StringIO()
    call_command("analyze_photos", *args, stdout=out)
    return out.getvalue()


class AnalyzePhotosCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.dated = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        self.undated = upload(self.user, "b.png", png_bytes())
        upload(self.user, "c.txt", b"not a photo")

    def test_dry_run_counts_and_writes_nothing(self):
        with patch("workspace.photos.tasks.analyze_photo.apply_async") as queue:
            output = _run("--dry-run")

        self.assertIn("Would analyze 2 file(s).", output)
        queue.assert_not_called()
        self.assertFalse(MediaItem.objects.exists())

    def test_dry_run_honours_the_limit(self):
        self.assertIn("Would analyze 1 file(s).", _run("--dry-run", "--limit", "1"))

    def test_sync_fills_the_library(self):
        output = _run("--sync")

        self.assertIn("Analyzed 2 file(s).", output)
        self.assertEqual(
            set(MediaItem.objects.values_list("file_id", flat=True)),
            {self.dated.pk, self.undated.pk},
        )

    def test_idempotent(self):
        _run("--sync")
        before = dict(MediaItem.objects.values_list("file_id", "analyzed_at"))

        self.assertIn("Analyzed 0 file(s).", _run("--sync"))
        self.assertEqual(
            dict(MediaItem.objects.values_list("file_id", "analyzed_at")), before
        )

    def test_limit(self):
        self.assertIn("Analyzed 1 file(s).", _run("--sync", "--limit", "1"))
        self.assertEqual(MediaItem.objects.count(), 1)

    def test_default_queues_one_task_per_file(self):
        with patch("workspace.photos.tasks.analyze_photo.apply_async") as queue:
            output = _run()

        self.assertIn("Queued 2 file(s).", output)
        self.assertEqual(
            {c.kwargs["args"][0] for c in queue.call_args_list},
            {str(self.dated.pk), str(self.undated.pk)},
        )
        for call in queue.call_args_list:
            self.assertEqual(call.kwargs["kwargs"], {"reanalyze": False})
            self.assertEqual(call.kwargs["priority"], ANALYSIS_PRIORITY)

    def test_queued_reanalysis_reads_up_to_date_rows(self):
        analyze_media(self.dated)
        with patch("workspace.photos.tasks.analyze_photo.apply_async") as queue:
            _run("--reanalyze")

        self.assertEqual(
            {c.kwargs["kwargs"]["reanalyze"] for c in queue.call_args_list}, {True}
        )

    def test_reanalyze_takes_up_to_date_rows_too(self):
        analyze_media(self.dated)
        analyze_media(self.undated)

        self.assertIn("Would analyze 0 file(s).", _run("--dry-run"))
        self.assertIn("Would analyze 2 file(s).", _run("--dry-run", "--reanalyze"))

    def test_sync_reanalyze_reads_up_to_date_rows_again(self):
        analyze_media(self.dated)
        analyze_media(self.undated)
        MediaItem.objects.update(taken_at=None)

        self.assertIn("Analyzed 2 file(s).", _run("--sync", "--reanalyze"))
        self.assertIsNotNone(MediaItem.objects.get(file=self.dated).taken_at)

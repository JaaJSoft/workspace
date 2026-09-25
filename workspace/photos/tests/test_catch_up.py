"""The photo library's reader, run by the hourly catch-up (files.catch_up)."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from workspace.files.services.catch_up import get_catch_up
from workspace.files.tasks import catch_up as catch_up_task
from workspace.files.tasks import catch_up_file
from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import analyze_media, pending_media_qs

from .images import jpeg_bytes, png_bytes, upload

User = get_user_model()


def catch_up():
    """Run the hourly pass and every analysis it queued; return how many it queued."""
    with patch.object(catch_up_file, "apply_async") as queue:
        stats = catch_up_task.apply().get()
    for call in queue.call_args_list:
        if call.kwargs["args"][0] == "photos":
            catch_up_file.apply(args=call.kwargs["args"])
    return stats["photos"]


def analyze(file_obj, **kwargs):
    return catch_up_file.apply(args=["photos", str(file_obj.pk)], kwargs=kwargs).get()


class PhotosCatchUpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_registered_with_the_catch_up(self):
        self.assertIs(get_catch_up("photos").process, analyze_media)

    def test_fills_the_library_and_is_idempotent(self):
        dated = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        undated = upload(self.user, "b.png", png_bytes())
        upload(self.user, "c.txt", b"not a photo")

        self.assertEqual(catch_up(), 2)
        self.assertEqual(
            set(MediaItem.objects.values_list("file_id", flat=True)),
            {dated.pk, undated.pk},
        )
        self.assertEqual(catch_up(), 0)

    def test_rows_from_an_older_reader_are_read_again_once(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        analyze_media(f)
        MediaItem.objects.filter(file=f).update(analysis_version=None, taken_at=None)

        self.assertEqual(catch_up(), 1)
        self.assertIsNotNone(MediaItem.objects.get(file=f).taken_at)
        self.assertEqual(catch_up(), 0)

    def test_a_file_analyzed_since_it_was_queued_is_not_read_again(self):
        """Its upload event may have run while the task waited in the queue."""
        f = upload(self.user, "a.jpg")
        analyze_media(f)

        with patch("workspace.photos.services.analysis.read_metadata") as read:
            self.assertEqual(analyze(f), {"status": "skipped"})
        read.assert_not_called()

    def test_reanalyze_reads_an_up_to_date_file_again(self):
        f = upload(self.user, "a.jpg")
        analyze_media(f)

        self.assertEqual(analyze(f, reanalyze=True), {"status": "ok"})

    def test_an_unreadable_blob_is_skipped_and_stays_pending(self):
        f = upload(self.user, "a.jpg")
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.photos.services.analysis", "WARNING"):
            self.assertEqual(analyze(f), {"status": "skipped"})
        self.assertTrue(pending_media_qs().filter(pk=f.pk).exists())

    def test_a_non_photo_is_skipped(self):
        f = upload(self.user, "a.txt", b"text")

        self.assertEqual(analyze(f), {"status": "skipped"})

    def test_command_reanalyzes_the_library(self):
        dated = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        analyze_media(dated)
        MediaItem.objects.update(taken_at=None)
        out = StringIO()

        call_command("catch_up", "photos", "--sync", "--reanalyze", stdout=out)

        self.assertIn("photos: processed 1 file(s).", out.getvalue())
        self.assertIsNotNone(MediaItem.objects.get(file=dated).taken_at)

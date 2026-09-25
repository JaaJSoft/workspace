from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import pending_media_qs
from workspace.photos.tasks import (
    ANALYSIS_PRIORITY,
    CATCH_UP_EXPIRES,
    analyze_pending,
    analyze_photo,
)

from .images import jpeg_bytes, png_bytes, upload

User = get_user_model()


def catch_up():
    """Run the hourly pass, then every analysis it queued; return the queue calls."""
    with patch.object(analyze_photo, "apply_async") as queue:
        stats = analyze_pending.apply().get()
    for call in queue.call_args_list:
        analyze_photo.apply(args=call.kwargs["args"], kwargs=call.kwargs.get("kwargs"))
    return stats, queue.call_args_list


class AnalyzePendingTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_fills_the_library_and_is_idempotent(self):
        dated = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        undated = upload(self.user, "b.png", png_bytes())
        upload(self.user, "c.txt", b"not a photo")

        self.assertEqual(catch_up()[0], {"queued": 2})
        self.assertEqual(
            set(MediaItem.objects.values_list("file_id", flat=True)),
            {dated.pk, undated.pk},
        )
        self.assertEqual(catch_up()[0], {"queued": 0})

    def test_queues_one_low_priority_task_per_file_expiring_with_the_pass(self):
        """The backlog never holds up other tasks, and what the workers did not
        reach before the next pass is dropped rather than queued twice."""
        a = upload(self.user, "a.jpg")
        b = upload(self.user, "b.jpg")

        _, calls = catch_up()

        self.assertEqual({c.kwargs["args"][0] for c in calls}, {str(a.pk), str(b.pk)})
        for call in calls:
            self.assertEqual(call.kwargs["priority"], ANALYSIS_PRIORITY)
            self.assertEqual(call.kwargs["expires"], CATCH_UP_EXPIRES)

    def test_rows_from_an_older_reader_are_read_again_once(self):
        f = upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        analyze_photo.apply(args=[str(f.pk)])
        MediaItem.objects.filter(file=f).update(analysis_version=None, taken_at=None)

        self.assertEqual(catch_up()[0], {"queued": 1})
        self.assertIsNotNone(MediaItem.objects.get(file=f).taken_at)
        self.assertEqual(catch_up()[0], {"queued": 0})

    @patch("workspace.photos.tasks.CATCH_UP_LIMIT", 1)
    def test_one_pass_is_bounded(self):
        """A backlog larger than one pass drains over the following ones."""
        upload(self.user, "a.jpg")
        upload(self.user, "b.jpg")

        self.assertEqual(catch_up()[0], {"queued": 1})
        self.assertEqual(MediaItem.objects.count(), 1)
        self.assertEqual(catch_up()[0], {"queued": 1})
        self.assertEqual(MediaItem.objects.count(), 2)


class AnalyzePhotoTaskTests(TestCase):
    def test_analyzes_the_file(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.jpg")

        self.assertEqual(analyze_photo.apply(args=[str(f.pk)]).get(), {"status": "ok"})
        self.assertTrue(MediaItem.objects.filter(file=f).exists())

    def test_a_file_analyzed_since_it_was_queued_is_not_read_again(self):
        """Its upload event may have run while the task waited in the queue."""
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.jpg")
        analyze_photo.apply(args=[str(f.pk)])

        with patch("workspace.photos.services.analysis.read_metadata") as read:
            status = analyze_photo.apply(args=[str(f.pk)]).get()

        self.assertEqual(status, {"status": "skipped"})
        read.assert_not_called()

    def test_reanalyze_reads_an_up_to_date_file_again(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.jpg")
        analyze_photo.apply(args=[str(f.pk)])

        status = analyze_photo.apply(args=[str(f.pk)], kwargs={"reanalyze": True})

        self.assertEqual(status.get(), {"status": "ok"})

    def test_an_unreadable_blob_is_skipped_and_stays_pending(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.jpg")
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.photos.services.analysis", "WARNING"):
            status = analyze_photo.apply(args=[str(f.pk)]).get()

        self.assertEqual(status, {"status": "skipped"})
        self.assertTrue(pending_media_qs().filter(pk=f.pk).exists())

    def test_a_non_photo_is_skipped(self):
        user = User.objects.create_user(username="alice", password="p")
        f = upload(user, "a.txt", b"text")

        self.assertEqual(
            analyze_photo.apply(args=[str(f.pk)]).get(), {"status": "skipped"}
        )

    def test_unknown_or_malformed_id(self):
        for file_uuid in ("00000000-0000-0000-0000-000000000000", "nope", None):
            with self.subTest(file_uuid=file_uuid):
                self.assertEqual(
                    analyze_photo.apply(args=[file_uuid]).get(),
                    {"status": "not_found"},
                )


class BeatScheduleTests(SimpleTestCase):
    def test_catch_up_runs_hourly(self):
        entry = settings.CELERY_BEAT_SCHEDULE["analyze-photos"]

        self.assertEqual(entry["task"], "photos.analyze_pending")
        self.assertEqual(entry["schedule"], 3600.0)

    def test_a_tick_that_never_started_is_dropped(self):
        """No second pass stacks up behind one that is still reading blobs."""
        entry = settings.CELERY_BEAT_SCHEDULE["analyze-photos"]

        self.assertEqual(entry["options"], {"expires": 3600.0})

    def test_queued_analyses_expire_at_the_next_pass(self):
        entry = settings.CELERY_BEAT_SCHEDULE["analyze-photos"]

        self.assertEqual(CATCH_UP_EXPIRES, entry["schedule"])

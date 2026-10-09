from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import FileService
from workspace.files.services.event_dispatch import run_handlers
from workspace.files.services.processors import get_processor, run_pipeline
from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import (
    analyze_media,
    is_media_candidate,
    refresh_media_item,
)
from workspace.photos.services.face_analysis import is_face_candidate

from .images import jpeg_bytes, png_bytes, upload

User = get_user_model()


class ProcessorRegistrationTests(TestCase):
    def test_the_library_runs_in_the_upload_pipeline(self):
        processor = get_processor("photos")

        self.assertIs(processor.applies_to, is_media_candidate)
        self.assertIs(processor.process, refresh_media_item)

    def test_faces_are_queued_rather_than_run_in_the_pipeline(self):
        processor = get_processor("faces")

        self.assertIs(processor.applies_to, is_face_candidate)
        self.assertIsNotNone(processor.enqueue)


class UploadDispatchTests(TestCase):
    """The real path: a write records an event, its handlers run on commit."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _write(self, fn):
        # The handlers run in a Celery task after commit; run them inline.
        with patch(
            "workspace.files.tasks.run_file_event_handlers.delay",
            side_effect=run_handlers,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                return fn()

    def test_upload_with_exif_lands_on_its_capture_date(self):
        f = self._write(
            lambda: upload(
                self.user,
                "beach.jpg",
                jpeg_bytes(taken="2024:07:14 18:32:05", offset="+02:00"),
            )
        )

        self.assertEqual(
            MediaItem.objects.get(file=f).taken_at,
            datetime(2024, 7, 14, 16, 32, 5, tzinfo=UTC),
        )

    def test_upload_without_exif_is_undated(self):
        f = self._write(lambda: upload(self.user, "scan.png", png_bytes()))

        self.assertIsNone(MediaItem.objects.get(file=f).taken_at)

    def test_replacing_the_content_refreshes_the_row(self):
        f = self._write(
            lambda: upload(self.user, "a.jpg", jpeg_bytes(taken="2024:07:14 18:32:05"))
        )

        self._write(
            lambda: FileService.update_content(
                f,
                ContentFile(jpeg_bytes(taken="2025:01:02 09:00:00"), name="a.jpg"),
                acting_user=self.user,
            )
        )

        f.refresh_from_db()
        photo = MediaItem.objects.get(file=f)
        self.assertEqual(photo.taken_at, datetime(2025, 1, 2, 9, tzinfo=UTC))
        self.assertEqual(photo.content_hash, f.content_hash)


class PipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="p")

    def test_trashed_before_the_pipeline_ran_is_skipped(self):
        f = upload(self.user, "a.jpg")
        FileService.soft_delete(f, acting_user=self.user)
        f.refresh_from_db()

        run_pipeline(f.uuid)

        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_a_photo_overwritten_with_something_else_leaves_the_library(self):
        f = upload(self.user, "a.jpg")
        analyze_media(f)
        FileService.update_content(f, ContentFile(b"plain text now", name="a.jpg"))
        f.refresh_from_db()
        self.assertNotEqual(f.type, "jpeg")

        run_pipeline(f.uuid)

        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_a_non_image_upload_writes_nothing(self):
        f = upload(self.user, "a.txt", b"hello")

        run_pipeline(f.uuid)

        self.assertFalse(MediaItem.objects.exists())

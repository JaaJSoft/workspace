from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import FileEvent
from workspace.files.services import FileService
from workspace.files.services.event_dispatch import _HANDLERS, run_handlers
from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import analyze_photo
from workspace.photos.services.handlers import analyze_photo_for_event

from .images import jpeg_bytes, png_bytes, upload

User = get_user_model()


class HandlerRegistrationTests(TestCase):
    def test_subscribed_to_uploads_and_content_replacements(self):
        for action in (FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED):
            with self.subTest(action=action):
                self.assertIn(analyze_photo_for_event, _HANDLERS[str(action)])


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


class HandlerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="p")

    def _event(self, file_obj, action):
        return FileEvent.objects.create(file=file_obj, actor=self.user, action=action)

    def test_trashed_before_the_handler_ran_is_skipped(self):
        f = upload(self.user, "a.jpg")
        FileService.soft_delete(f, acting_user=self.user)
        f.refresh_from_db()

        analyze_photo_for_event(self._event(f, FileEvent.Action.CREATED))

        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_a_photo_overwritten_with_something_else_leaves_the_library(self):
        f = upload(self.user, "a.jpg")
        analyze_photo(f)
        FileService.update_content(f, ContentFile(b"plain text now", name="a.jpg"))
        f.refresh_from_db()
        self.assertNotEqual(f.type, "jpeg")

        analyze_photo_for_event(self._event(f, FileEvent.Action.CONTENT_REPLACED))

        self.assertFalse(MediaItem.objects.filter(file=f).exists())

    def test_a_non_image_upload_writes_nothing(self):
        f = upload(self.user, "a.txt", b"hello")

        analyze_photo_for_event(self._event(f, FileEvent.Action.CREATED))

        self.assertFalse(MediaItem.objects.exists())

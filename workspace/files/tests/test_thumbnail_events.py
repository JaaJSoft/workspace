import io

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from workspace.files.models import FileEvent
from workspace.files.services import FileService
from workspace.files.services.thumbnail_events import generate_thumbnail_for_event
from workspace.files.services.thumbnails import get_thumbnail_path

User = get_user_model()


def _png_bytes(size=(800, 600)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class ThumbnailEventHandlerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="th-evt", password="p")

    def _event(self, file, action=FileEvent.Action.CREATED):
        return FileEvent.objects.create(file=file, actor=self.user, action=action)

    def _cleanup(self, uuid):
        path = get_thumbnail_path(uuid)
        try:
            if default_storage.exists(path):
                default_storage.delete(path)
        except PermissionError, OSError:
            # Best-effort cleanup: a blocked or unavailable delete
            # (e.g. Windows file lock) must not fail the test run.
            pass

    def test_image_create_generates_thumbnail_and_sets_flag(self):
        f = FileService.create_file(
            owner=self.user,
            name="pic.png",
            content=ContentFile(_png_bytes(), name="pic.png"),
            mime_type="image/png",
        )
        f.type = "png"
        f.save(update_fields=["type"])
        f.has_thumbnail = False
        f.save(update_fields=["has_thumbnail"])
        self.addCleanup(self._cleanup, f.uuid)

        generate_thumbnail_for_event(self._event(f))

        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)
        self.assertTrue(default_storage.exists(get_thumbnail_path(f.uuid)))

    def test_content_replaced_generates_thumbnail_and_sets_flag(self):
        f = FileService.create_file(
            owner=self.user,
            name="pic3.png",
            content=ContentFile(_png_bytes(), name="pic3.png"),
            mime_type="image/png",
        )
        f.type = "png"
        f.save(update_fields=["type"])
        f.has_thumbnail = False
        f.save(update_fields=["has_thumbnail"])
        self.addCleanup(self._cleanup, f.uuid)

        generate_thumbnail_for_event(self._event(f, FileEvent.Action.CONTENT_REPLACED))

        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)
        self.assertTrue(default_storage.exists(get_thumbnail_path(f.uuid)))

    def test_non_image_is_skipped(self):
        f = FileService.create_file(
            owner=self.user,
            name="note.txt",
            content=ContentFile(b"hello", name="note.txt"),
            mime_type="text/plain",
        )
        f.type = "text"
        f.save(update_fields=["type"])
        self.addCleanup(self._cleanup, f.uuid)

        generate_thumbnail_for_event(self._event(f))

        f.refresh_from_db()
        self.assertFalse(f.has_thumbnail)
        self.assertFalse(default_storage.exists(get_thumbnail_path(f.uuid)))

    def test_trashed_file_is_skipped(self):
        f = FileService.create_file(
            owner=self.user,
            name="pic2.png",
            content=ContentFile(_png_bytes(), name="pic2.png"),
            mime_type="image/png",
        )
        f.type = "png"
        f.save(update_fields=["type"])
        FileService.soft_delete(f, acting_user=self.user)
        f.refresh_from_db()
        self.addCleanup(self._cleanup, f.uuid)

        generate_thumbnail_for_event(self._event(f))

        self.assertFalse(default_storage.exists(get_thumbnail_path(f.uuid)))

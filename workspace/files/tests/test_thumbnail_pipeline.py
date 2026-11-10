import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from workspace.files.models import File
from workspace.files.services import trash, tree
from workspace.files.services.content_writes import update_content
from workspace.files.services.processors import run_pipeline
from workspace.files.services.thumbnails.generation import (
    get_thumbnail_path,
    refresh_thumbnail,
)

User = get_user_model()


def _png_bytes(size=(800, 600)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class ThumbnailPipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="th-evt", password="p")

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
        f = tree.create_file(
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

        run_pipeline(f.uuid)

        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)
        self.assertTrue(default_storage.exists(get_thumbnail_path(f.uuid)))

    def test_content_replaced_generates_thumbnail_and_sets_flag(self):
        f = tree.create_file(
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

        run_pipeline(f.uuid)

        f.refresh_from_db()
        self.assertTrue(f.has_thumbnail)
        self.assertTrue(default_storage.exists(get_thumbnail_path(f.uuid)))

    @patch("workspace.files.sse_provider.push_file_event")
    def test_a_drawn_thumbnail_is_announced_before_the_file_settles(self, push):
        f = tree.create_file(
            owner=self.user,
            name="pic4.png",
            content=ContentFile(_png_bytes(), name="pic4.png"),
            mime_type="image/png",
        )
        self.addCleanup(self._cleanup, f.uuid)

        run_pipeline(f.uuid)

        events = [call.args[1] for call in push.call_args_list]
        self.assertEqual(events, ["preview_ready", "processing_settled"])

    def test_non_image_is_skipped(self):
        f = tree.create_file(
            owner=self.user,
            name="note.txt",
            content=ContentFile(b"hello", name="note.txt"),
            mime_type="text/plain",
        )
        f.type = "text"
        f.save(update_fields=["type"])
        self.addCleanup(self._cleanup, f.uuid)

        run_pipeline(f.uuid)

        f.refresh_from_db()
        self.assertFalse(f.has_thumbnail)
        self.assertFalse(default_storage.exists(get_thumbnail_path(f.uuid)))

    def test_trashed_file_is_skipped(self):
        f = tree.create_file(
            owner=self.user,
            name="pic2.png",
            content=ContentFile(_png_bytes(), name="pic2.png"),
            mime_type="image/png",
        )
        f.type = "png"
        f.save(update_fields=["type"])
        trash.soft_delete(f, acting_user=self.user)
        f.refresh_from_db()
        self.addCleanup(self._cleanup, f.uuid)

        run_pipeline(f.uuid)

        self.assertFalse(default_storage.exists(get_thumbnail_path(f.uuid)))


class ThumbnailFlagWriteTests(TestCase):
    """The flag lands on the row as it is now, not as the task loaded it."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="th-flag", password="p")

    def setUp(self):
        self.file = tree.create_file(
            owner=self.user,
            name="before.png",
            content=ContentFile(_png_bytes(), name="before.png"),
            mime_type="image/png",
        )

    def _refresh_while(self, concurrent_write):
        """Run refresh_thumbnail on a snapshot, *concurrent_write* landing mid-draw."""
        stale = File.objects.get(pk=self.file.pk)

        def draw(file_obj):
            concurrent_write(File.objects.get(pk=self.file.pk))
            return True

        with patch(
            "workspace.files.services.thumbnails.generation.generate_thumbnail",
            side_effect=draw,
        ):
            return refresh_thumbnail(stale)

    def test_a_rename_during_the_draw_keeps_its_path(self):
        self._refresh_while(
            lambda current: tree.rename(current, "after.png", acting_user=self.user)
        )

        row = File.objects.get(pk=self.file.pk)
        self.assertEqual(row.name, "after.png")
        self.assertEqual(row.path, "after.png")
        self.assertTrue(row.has_thumbnail)

    def test_a_thumbnail_of_replaced_bytes_does_not_flag_the_new_ones(self):
        """The replacement cleared the flag; the late draw of the old bytes
        must leave it cleared, so the new bytes still get a thumbnail."""
        stored = self._refresh_while(
            lambda current: update_content(
                current,
                ContentFile(_png_bytes((40, 30)), name="before.png"),
                acting_user=self.user,
            )
        )

        self.assertFalse(stored)
        self.assertFalse(File.objects.get(pk=self.file.pk).has_thumbnail)

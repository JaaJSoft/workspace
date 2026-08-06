"""Regression tests for viewer pinning on upload.

Magika identifies a container, not the tracks inside it: an audio-only MP4
(a ``.m4a``) is reported as video. The declared media type is the only signal
that can tell them apart, and acting on it is a display decision - so it pins
a viewer instead of rewriting the detected category. See
``pin_viewer_for_upload`` in ``workspace/files/services/filetype.py``.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from workspace.files.services import FileService

User = get_user_model()

MP4_HEADER = (
    b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41\x00\x00\x00\x08free"
) + b"\x00" * 512


class FileViewerPinningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pw"
        )

    def test_audio_only_container_is_pinned(self):
        """An .m4a is an MP4 container; Magika reads it as video."""
        content = SimpleUploadedFile("voice.m4a", MP4_HEADER, content_type="audio/mp4")
        f = FileService.create_file(
            owner=self.user, name="voice.m4a", content=content, acting_user=self.user
        )
        self.assertEqual(f.viewer, "audio")

    def test_detection_is_left_untouched(self):
        """The pin is a display decision: the detected category must not move."""
        content = SimpleUploadedFile("voice.m4a", MP4_HEADER, content_type="audio/mp4")
        f = FileService.create_file(
            owner=self.user, name="voice.m4a", content=content, acting_user=self.user
        )
        self.assertEqual(f.category, "video")
        self.assertEqual(f.mime_type, "video/mp4")

    def test_ordinary_upload_is_not_pinned(self):
        content = SimpleUploadedFile(
            "note.txt", b"hello\n" * 40, content_type="text/plain"
        )
        f = FileService.create_file(
            owner=self.user, name="note.txt", content=content, acting_user=self.user
        )
        self.assertEqual(f.viewer, "")

    def test_update_content_repins(self):
        content = SimpleUploadedFile("clip.bin", MP4_HEADER, content_type="video/mp4")
        f = FileService.create_file(
            owner=self.user, name="clip.bin", content=content, acting_user=self.user
        )
        self.assertEqual(f.viewer, "")
        FileService.update_content(
            f,
            SimpleUploadedFile("clip.bin", MP4_HEADER, content_type="audio/mp4"),
            acting_user=self.user,
        )
        f.refresh_from_db()
        self.assertEqual(f.viewer, "audio")

    def test_an_explicit_pin_skips_derivation(self):
        """A copy carries its source's pin: the declared media type no longer
        holds the information (the row is stored as video/mp4)."""
        content = SimpleUploadedFile("clip.m4a", MP4_HEADER, content_type="video/mp4")
        f = FileService.create_file(
            owner=self.user,
            name="clip.m4a",
            content=content,
            viewer="audio",
            acting_user=self.user,
        )
        self.assertEqual(f.viewer, "audio")

    def test_copy_preserves_the_pin(self):
        content = SimpleUploadedFile("voice.m4a", MP4_HEADER, content_type="audio/mp4")
        source = FileService.create_file(
            owner=self.user, name="voice.m4a", content=content, acting_user=self.user
        )
        folder = FileService.create_folder(
            owner=self.user, name="Copies", acting_user=self.user
        )

        copied = FileService.copy(source, folder, self.user, acting_user=self.user)

        self.assertEqual(copied.viewer, "audio")
        self.assertNotEqual(copied.content.name, source.content.name)


class FileViewerResolutionTests(TestCase):
    """The viewer modal must honour the pinned slug, not just the content type."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="viewer_user", email="viewer@test.com", password="pw"
        )
        self.client.force_login(self.user)

    def _pinned_file(self, viewer):
        f = FileService.create_file(
            owner=self.user,
            name="voice.m4a",
            content=SimpleUploadedFile(
                "voice.m4a", MP4_HEADER, content_type="audio/mp4"
            ),
            acting_user=self.user,
        )
        f.viewer = viewer
        f.save(update_fields=["viewer"])
        return f

    def _view_html(self, file_obj):
        resp = self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": file_obj.uuid})
        )
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_view_file_honours_the_pinned_audio_viewer(self):
        html = self._view_html(self._pinned_file("audio"))
        self.assertIn("<audio", html)
        self.assertNotIn("<video", html)

    def test_view_file_resolves_by_content_without_a_pin(self):
        """An .m4a Magika read as an MP4 container: no pin, video viewer."""
        html = self._view_html(self._pinned_file(""))
        self.assertIn("<video", html)

    def test_view_file_falls_back_on_an_unknown_pin(self):
        """A slug left over from a removed viewer must not break the modal."""
        html = self._view_html(self._pinned_file("no-such-viewer"))
        self.assertIn("<video", html)

    def test_shared_file_view_honours_the_pinned_audio_viewer(self):
        from workspace.files.models import FileShareLink

        f = self._pinned_file("audio")
        link = FileShareLink.objects.create(file=f, created_by=self.user)
        resp = self.client.get(
            reverse("files_ui:shared_file", kwargs={"token": link.token})
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("<audio", html)
        self.assertNotIn("<video", html)

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

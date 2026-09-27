"""What a vision model is shown of a video attachment, read by the real ffmpeg."""

import base64
import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase
from PIL import Image

from workspace.ai.services.video import extract_video_frames
from workspace.files.services import FileService
from workspace.files.tests.videos import clip_bytes, requires_ffmpeg

User = get_user_model()


class _Attachment:
    """The part of chat.MessageAttachment the extraction reads."""

    def __init__(self, stored_file, name):
        self.file = stored_file
        self.original_name = name
        self.uuid = "00000000-0000-0000-0000-000000000001"


def _long_clip(seconds):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "long.mkv"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=duration={seconds}:size=64x36:rate=2",
                "-c:v",
                "ffv1",
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        return target.read_bytes()


def _frame(part):
    url = part["image_url"]["url"]
    prefix = "data:image/jpeg;base64,"
    assert url.startswith(prefix)
    return Image.open(io.BytesIO(base64.b64decode(url[len(prefix) :])))


@requires_ffmpeg
class ExtractVideoFramesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _attachment(self, name, data):
        stored = FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data)
        )
        return _Attachment(stored.content, name)

    def test_a_short_video_is_one_frame_per_second(self):
        parts, description = extract_video_frames(
            self._attachment("clip.webm", clip_bytes("clip.webm"))
        )

        self.assertEqual(len(parts), 3)
        with _frame(parts[0]) as frame:
            self.assertEqual(frame.format, "JPEG")
            self.assertEqual(frame.size, (96, 54))
        self.assertIn('"clip.webm"', description)
        self.assertIn("duration: 3s", description)
        self.assertIn("3 frames", description)

    def test_frames_are_shown_the_way_a_player_shows_them(self):
        # Stored 96x54 with a 90 degree display matrix: shown in portrait.
        parts, _description = extract_video_frames(
            self._attachment("clip.mov", clip_bytes("clip_iphone.mov"))
        )

        with _frame(parts[0]) as frame:
            self.assertEqual(frame.size, (54, 96))

    def test_a_long_video_is_capped_and_spread_over_its_length(self):
        parts, description = extract_video_frames(
            self._attachment("long.mkv", _long_clip(90))
        )

        self.assertEqual(len(parts), 30)
        self.assertIn("duration: 90s", description)
        self.assertIn("1 frame every 3.0s", description)

    def test_something_that_is_not_a_video_shows_nothing(self):
        with self.assertLogs("workspace.ai.services.video", level="WARNING"):
            parts, description = extract_video_frames(
                self._attachment("clip.mp4", b"not a video")
            )

        self.assertEqual(parts, [])
        self.assertIsNone(description)


class WithoutFfmpegTests(SimpleTestCase):
    @patch("workspace.files.services.ffmpeg.FFMPEG", None)
    def test_nothing_is_shown_and_nothing_is_read(self):
        attachment = _Attachment(MagicMock(), "clip.mp4")

        self.assertEqual(extract_video_frames(attachment), ([], None))
        attachment.file.open.assert_not_called()

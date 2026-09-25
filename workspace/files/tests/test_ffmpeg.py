import io
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase
from PIL import Image

from workspace.files.services import FileService, ffmpeg

from .videos import clip_bytes, requires_ffmpeg

User = get_user_model()


class _RemoteFieldFile:
    """A stored file whose backend has no local path, as an object store."""

    def __init__(self, data):
        self._data = data
        self.size = len(data)

    @property
    def path(self):
        raise NotImplementedError

    def open(self, mode="rb"):
        return io.BytesIO(self._data)


class LocalPathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_local_storage_hands_out_the_blob_itself(self):
        f = FileService.create_file(
            owner=self.user, name="clip.webm", content=ContentFile(b"bytes")
        )

        with ffmpeg.local_path(f.content) as path:
            self.assertEqual(path, f.content.path)

    def test_a_missing_blob_is_file_not_found(self):
        f = FileService.create_file(
            owner=self.user, name="clip.webm", content=ContentFile(b"bytes")
        )
        f.content.storage.delete(f.content.name)

        with self.assertRaises(FileNotFoundError):
            with ffmpeg.local_path(f.content):
                pass

    def test_other_backends_get_a_temporary_copy(self):
        with ffmpeg.local_path(_RemoteFieldFile(b"video bytes")) as path:
            with open(path, "rb") as copy:
                self.assertEqual(copy.read(), b"video bytes")

        self.assertFalse(os.path.exists(path))

    def test_a_copy_past_the_bound_is_refused(self):
        with self.assertRaises(ffmpeg.MediaToolError):
            with ffmpeg.local_path(_RemoteFieldFile(b"12345"), max_bytes=4):
                pass


class MissingToolsTests(SimpleTestCase):
    @patch("workspace.files.services.ffmpeg.FFPROBE", None)
    def test_probe_without_ffprobe(self):
        with self.assertRaises(ffmpeg.MediaToolError):
            ffmpeg.probe("/tmp/clip.mp4")

    @patch("workspace.files.services.ffmpeg.FFMPEG", None)
    def test_frame_without_ffmpeg(self):
        with self.assertRaises(ffmpeg.MediaToolError):
            ffmpeg.extract_frame("/tmp/clip.mp4", at=1, max_size=(64, 64))


class DurationTests(SimpleTestCase):
    def test_reads_the_container_duration(self):
        self.assertEqual(ffmpeg.duration({"format": {"duration": "3.5"}}), 3.5)

    def test_missing_zero_or_garbage_is_none(self):
        for report in (
            {},
            {"format": {}},
            {"format": {"duration": "0"}},
            {"format": {"duration": "N/A"}},
        ):
            with self.subTest(report=report):
                self.assertIsNone(ffmpeg.duration(report))


@requires_ffmpeg
class RealToolsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _clip_path(self, name):
        f = FileService.create_file(
            owner=self.user, name=name, content=ContentFile(clip_bytes(name))
        )
        return f.content.path

    def test_probe_reports_streams_and_format(self):
        report = ffmpeg.probe(self._clip_path("clip.webm"))

        self.assertEqual(report["streams"][0]["codec_name"], "vp9")
        self.assertEqual(ffmpeg.duration(report), 3.0)

    def test_probe_of_something_that_is_not_a_video(self):
        f = FileService.create_file(
            owner=self.user, name="notes.txt", content=ContentFile(b"not a video")
        )

        with self.assertRaises(ffmpeg.MediaToolError):
            ffmpeg.probe(f.content.path)

    def test_frame_is_a_png_within_the_bounds(self):
        png = ffmpeg.extract_frame(
            self._clip_path("clip.webm"), at=1, max_size=(48, 48)
        )

        with Image.open(io.BytesIO(png)) as frame:
            self.assertEqual(frame.format, "PNG")
            self.assertEqual(frame.size, (48, 27))

    def test_frame_follows_the_rotation(self):
        # Stored 96x54 with a 90 degree display matrix: shown in portrait.
        png = ffmpeg.extract_frame(
            self._clip_path("clip_iphone.mov"), at=1, max_size=(512, 512)
        )

        with Image.open(io.BytesIO(png)) as frame:
            self.assertEqual(frame.size, (54, 96))

    def test_seek_past_the_end_yields_no_frame(self):
        png = ffmpeg.extract_frame(
            self._clip_path("clip.webm"), at=60, max_size=(48, 48)
        )

        self.assertEqual(png, b"")

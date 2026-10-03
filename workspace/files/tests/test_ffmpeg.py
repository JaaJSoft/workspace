import io
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase
from PIL import Image

from workspace.common.storage.backend import temporary_copy
from workspace.files.services import FileService, ffmpeg

from .videos import clip_bytes, requires_ffmpeg

User = get_user_model()


class _RemoteStorage:
    """A storage whose backend has no local path, as an object store."""

    def __init__(self, data):
        self._data = data

    def size(self, name):
        return len(self._data)

    def open(self, name, mode="rb"):
        return io.BytesIO(self._data)

    def local_path(self, name, *, max_bytes):
        return temporary_copy(self, name, max_bytes=max_bytes)


class _RemoteFieldFile:
    def __init__(self, data):
        self.name = "clip.webm"
        self.storage = _RemoteStorage(data)


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

    @patch("workspace.files.services.ffmpeg.FFMPEG", None)
    def test_sampling_without_ffmpeg(self):
        with self.assertRaises(ffmpeg.MediaToolError):
            ffmpeg.sample_frames(
                "/tmp/clip.mp4", print, size=(64, 64), interval=2, max_frames=1
            )


class StartTimeTests(SimpleTestCase):
    def test_reads_the_container_start(self):
        self.assertEqual(ffmpeg.start_time({"format": {"start_time": "1.4"}}), 1.4)

    def test_missing_negative_or_garbage_is_zero(self):
        for report in (
            {},
            {"format": {"start_time": "-0.02"}},
            {"format": {"start_time": "N/A"}},
        ):
            with self.subTest(report=report):
                self.assertEqual(ffmpeg.start_time(report), 0.0)


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


@requires_ffmpeg
class SampleFramesTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="alice", password="p")
        self.clip = FileService.create_file(
            owner=user, name="clip.webm", content=ContentFile(clip_bytes("clip.webm"))
        ).content.path

    def _sample(self, path=None, **kwargs):
        frames = []
        times = ffmpeg.sample_frames(
            path or self.clip,
            lambda index, pixels: frames.append((index, len(pixels))),
            **{"size": (48, 27), "interval": 1, "max_frames": 60, **kwargs},
        )
        return times, frames

    def test_hands_over_frames_of_the_requested_size_at_the_interval(self):
        times, frames = self._sample()

        self.assertEqual(times, [0.0, 1.0, 2.0])
        self.assertEqual(frames, [(0, 48 * 27 * 3), (1, 48 * 27 * 3), (2, 48 * 27 * 3)])

    def test_stops_at_the_frame_budget(self):
        times, frames = self._sample(max_frames=2)

        self.assertEqual(times, [0.0, 1.0])
        self.assertEqual(len(frames), 2)

    def test_something_that_is_not_a_video(self):
        not_a_video = os.path.join(os.path.dirname(self.clip), "notes.txt")
        with open(not_a_video, "wb") as f:
            f.write(b"not a video")

        with self.assertRaises(ffmpeg.MediaToolError):
            self._sample(not_a_video)

    def test_a_failing_callback_stops_ffmpeg_and_propagates(self):
        def fail(index, pixels):
            raise RuntimeError("model crashed")

        with self.assertRaisesMessage(RuntimeError, "model crashed"):
            ffmpeg.sample_frames(
                self.clip, fail, size=(48, 27), interval=1, max_frames=60
            )

    def test_a_reading_past_the_timeout_is_an_error(self):
        with self.assertRaisesMessage(ffmpeg.MediaToolError, "took more than"):
            ffmpeg.sample_frames(
                self.clip,
                lambda index, pixels: __import__("time").sleep(0.2),
                size=(48, 27),
                interval=0.1,
                max_frames=60,
                timeout=0.1,
            )

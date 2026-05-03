"""Tests for the video frame extraction service."""

import subprocess
from unittest.mock import MagicMock, patch

from django.test import TestCase

from workspace.ai.services import video


class _FakeAttachment:
    """Minimal duck-typed stand-in for chat.MessageAttachment used by
    ``extract_video_frames``: only ``file.chuncks()`` and ``original_name``
    / ``uuid`` are accessed.
    """

    def __init__(self):
        self.original_name = 'test.mp4'
        self.uuid = '00000000-0000-0000-0000-000000000001'
        self.file = MagicMock()
        self.file.chunks.return_value = [b'fake-video-bytes']


class ExtractVideoFramesTests(TestCase):
    """Behavioural tests for ``video.extract_video_frames``."""

    def test_ffmpeg_invoked_with_check_true(self):
        """Regression: ``subprocess.run`` must be called with ``check=True``
        so a non-zero exit (corrupt video, missing codec, etc.) raises
        ``CalledProcessError`` instead of silently producing zero frames.
        The pre-fix code omitted ``check`` and the failure was invisible."""
        att = _FakeAttachment()

        # Real ffmpeg / ffprobe paths may or may not exist on the test host;
        # patch them so subprocess.run is the only thing under test here.
        with patch.object(video, '_FFMPEG', '/usr/bin/ffmpeg'), \
             patch.object(video, '_FFPROBE', '/usr/bin/ffprobe'), \
             patch.object(video, '_get_video_duration', return_value=10.0), \
             patch('workspace.ai.services.video.subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['ffmpeg'], returncode=0, stdout=b'', stderr=b'',
            )
            video.extract_video_frames(att)

        # Find the ffmpeg call (not ffprobe, which is patched out via
        # _get_video_duration). It must have check=True.
        ffmpeg_calls = [
            call for call in mock_run.call_args_list
            if call.args and call.args[0] and call.args[0][0] == '/usr/bin/ffmpeg'
        ]
        self.assertTrue(ffmpeg_calls, 'ffmpeg subprocess.run call expected')
        self.assertTrue(
            ffmpeg_calls[0].kwargs.get('check') is True,
            'ffmpeg must be invoked with check=True so non-zero exits raise',
        )

    def test_logs_warning_on_ffmpeg_nonzero_exit(self):
        """Companion to the check=True regression: when ffmpeg actually fails,
        the narrowed ``except (SubprocessError, OSError)`` block catches the
        ``CalledProcessError`` and logs a scrubbed warning, and the function
        degrades to ``([], None)`` without crashing the caller."""
        att = _FakeAttachment()

        with patch.object(video, '_FFMPEG', '/usr/bin/ffmpeg'), \
             patch.object(video, '_FFPROBE', '/usr/bin/ffprobe'), \
             patch.object(video, '_get_video_duration', return_value=10.0), \
             patch('workspace.ai.services.video.subprocess.run') as mock_run, \
             self.assertLogs('workspace.ai.services.video', level='WARNING') as log_ctx:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=['ffmpeg'], stderr=b'codec error',
            )
            parts, description = video.extract_video_frames(att)

        self.assertEqual(parts, [])
        self.assertIsNone(description)
        self.assertTrue(any(
            'Could not extract frames' in msg for msg in log_ctx.output
        ))

    def test_returns_empty_when_ffmpeg_missing(self):
        """When ffmpeg is not on PATH at import time, the function short-
        circuits to ``([], None)`` without invoking subprocess at all -
        avoids ``FileNotFoundError`` crashes in environments where ffmpeg
        is not installed."""
        att = _FakeAttachment()

        with patch.object(video, '_FFMPEG', None), \
             patch('workspace.ai.services.video.subprocess.run') as mock_run:
            parts, description = video.extract_video_frames(att)

        self.assertEqual(parts, [])
        self.assertIsNone(description)
        mock_run.assert_not_called()


class GetVideoDurationTests(TestCase):
    """Behavioural tests for the internal ``_get_video_duration`` helper."""

    def test_returns_none_when_ffprobe_missing(self):
        with patch.object(video, '_FFPROBE', None), \
             patch('workspace.ai.services.video.subprocess.run') as mock_run:
            self.assertIsNone(video._get_video_duration('/whatever'))
        mock_run.assert_not_called()

    def test_returns_none_on_subprocess_error(self):
        with patch.object(video, '_FFPROBE', '/usr/bin/ffprobe'), \
             patch('workspace.ai.services.video.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=['ffprobe'],
            )
            self.assertIsNone(video._get_video_duration('/whatever'))

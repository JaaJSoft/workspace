"""Video fixtures shared by the files and photos tests.

Short test patterns, a few KB each, in ``video_clips/`` (the videos are
three seconds of 96x54):

- ``clip.webm``: VP9, no audio. Plays in every browser Playwright drives.
- ``clip_hevc.mp4``: HEVC, no audio. What an iPhone records; Chromium on
  Linux cannot decode it.
- ``clip_iphone.mov``: H.264 with the metadata an iPhone writes: a 90 degree
  display rotation, a QuickTime creation date of 2024-07-14T12:30:01+0200, a
  location (48.8584, 2.2945) and the Apple make and model tags.
- ``clip.ogg``: two seconds of a 440 Hz tone in Opus, an audio file.
"""

import shutil
import unittest
from pathlib import Path

CLIPS = Path(__file__).resolve().parent / "video_clips"


def clip_bytes(name):
    return (CLIPS / name).read_bytes()


requires_ffmpeg = unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are not installed",
)

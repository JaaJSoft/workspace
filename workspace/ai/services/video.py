import base64
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_VIDEO_MAX_FRAMES = 30  # cap frames sent to the model to limit context size


def _get_video_duration(video_path):
    """Return video duration in seconds using ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _extract_video_frames(att):
    """Extract evenly-spaced frames from a video attachment (max _VIDEO_MAX_FRAMES).

    Returns (frame_parts, description) where frame_parts is a list of image_url
    content parts and description is a string summarising the video for the model.
    """
    parts = []
    description = None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, 'input.vid')
            with open(video_path, 'wb') as f:
                for chunk in att.file.chunks():
                    f.write(chunk)

            duration = _get_video_duration(video_path)
            if duration and duration > _VIDEO_MAX_FRAMES:
                fps = _VIDEO_MAX_FRAMES / duration
            else:
                fps = 1

            out_pattern = os.path.join(tmpdir, 'frame_%04d.jpg')
            subprocess.run(
                [
                    'ffmpeg', '-i', video_path,
                    '-vf', f'fps={fps}',
                    '-q:v', '8',
                    '-frames:v', str(_VIDEO_MAX_FRAMES),
                    out_pattern,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=120,
            )
            frame_files = sorted(
                f for f in os.listdir(tmpdir) if f.startswith('frame_') and f.endswith('.jpg')
            )
            if frame_files:
                dur_str = f'{duration:.0f}s' if duration else 'unknown duration'
                interval = duration / len(frame_files) if duration else 1
                description = (
                    f'The user attached a video: "{att.original_name}" '
                    f'(duration: {dur_str}). Since you cannot watch videos directly, '
                    f'it has been converted into {len(frame_files)} frames '
                    f'(1 frame every {interval:.1f}s) shown in chronological order '
                    f'in the next message. Analyze these frames to understand '
                    f'what happens in the video.'
                )
            for fname in frame_files:
                fpath = os.path.join(tmpdir, fname)
                with open(fpath, 'rb') as fh:
                    b64 = base64.b64encode(fh.read()).decode()
                parts.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
                })
    except Exception:
        logger.warning('Could not extract frames from video %s', att.uuid)
    return parts, description

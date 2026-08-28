"""Speech-to-text: what a voice message sent to a bot actually says.

The chat model has no ears, so a recording only reaches it as text. That text
is stored on the attachment the first time a reply needs it and replayed from
there afterwards, so each recording is transcribed once per conversation.

Nothing here announces a language. The recognition model identifies it from
the audio, unlike the speech models on the same backend, which reject a
language they do not know rather than falling back to detection.

Recordings arrive in whatever container the browser chose - MediaRecorder
offers webm/opus and mp4, never WAV - and the backend takes WAV alone
("only WAV audio uploads are currently supported for transcription"). So
anything that is not already RIFF/WAVE is transcoded here first.
"""

import io
import logging
import os
import shutil
import subprocess
import tempfile

from django.conf import settings

from workspace.common.logging import scrub

from ..client import get_transcription_client

logger = logging.getLogger(__name__)

# Resolved at import so the absolute path is captured once from the deploy's
# PATH and a later PATH change cannot redirect the call, as in
# ai/services/video.py. ffmpeg ships in the image for video frames already.
_FFMPEG = shutil.which("ffmpeg")
if not _FFMPEG:
    logger.info(
        "ffmpeg not found on PATH; a voice message that is not already WAV "
        "cannot be transcribed."
    )

# The recognition model reads 16 kHz mono 16-bit, and pinning the format here
# also pins what a converted recording can weigh: seconds * rate * width.
_SAMPLE_RATE = 16000
_SAMPLE_WIDTH = 2
_WAV_HEADER_SLACK = 4096


def is_transcription_enabled() -> bool:
    """Whether a recognition backend is configured.

    Deliberately not gated on ``AI_API_KEY``, like the speech side: the
    server has no authentication of its own, and reaching it directly on the
    internal network is a supported deployment.
    """
    return bool(
        settings.AI_ASR_MODEL and (settings.AI_ASR_BASE_URL or settings.AI_BASE_URL)
    )


def _is_wav(data: bytes) -> bool:
    """Whether *data* already carries a RIFF/WAVE header.

    Sniffed rather than read off the attachment's declared type: the browser
    labels the blob, and a wrong label would reach the backend as the 500 this
    check exists to avoid.
    """
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _to_wav(data: bytes) -> bytes | None:
    """*data* as 16 kHz mono PCM, or None when it could not be converted.

    None covers a deploy without ffmpeg, a container ffmpeg refuses, and a
    recording that decodes to more than the recorder itself allows; the caller
    turns any of them into "nothing heard" rather than a failed reply.

    Compression is why the length is capped rather than trusted. An upload is
    limited to 50 MB and a voice message to CHAT_VOICE_MAX_SECONDS, but that
    duration is only checked when the client declares one, and 50 MB of Opus
    at its lowest bitrate is around nineteen hours - some two gigabytes of PCM,
    read whole into a worker. AI_ASR_TIMEOUT does not bound that, since ffmpeg
    decodes far faster than real time.
    """
    if not _FFMPEG:
        return None
    seconds = settings.CHAT_VOICE_MAX_SECONDS
    budget = seconds * _SAMPLE_RATE * _SAMPLE_WIDTH + _WAV_HEADER_SLACK
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, "recording")
            target = os.path.join(tmpdir, "recording.wav")
            with open(source, "wb") as raw:
                raw.write(data)
            # check=True so a container ffmpeg cannot read raises instead of
            # leaving an empty file that the backend would reject downstream.
            # -t sits before -i so an overlong recording stops being decoded
            # at the cap, rather than being decoded whole and then trimmed.
            subprocess.run(
                [
                    _FFMPEG,
                    "-t",
                    str(seconds),
                    "-i",
                    source,
                    "-ac",
                    "1",
                    "-ar",
                    str(_SAMPLE_RATE),
                    "-c:a",
                    "pcm_s16le",
                    target,
                ],
                check=True,
                capture_output=True,
                timeout=settings.AI_ASR_TIMEOUT,
            )
            # Checked before the read, which is where the memory would go, in
            # case a container ever slips past -t.
            weight = os.path.getsize(target)
            if weight > budget:
                logger.warning(
                    "Refusing a voice message that converted to %d bytes "
                    "(budget %d for %d seconds)",
                    weight,
                    budget,
                    seconds,
                )
                return None
            with open(target, "rb") as converted:
                return converted.read()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Could not convert a voice message: %s", scrub(str(exc)))
        return None


def ai_transcribe_audio(data: bytes) -> str:
    """What the recognition model heard in *data*, or "" if it heard nothing.

    A backend failure returns "" rather than raising: the caller keeps
    whatever stand-in it already had for the recording, and the next turn of
    the conversation asks again. That is the whole retry policy.

    Raises:
        ValueError: If *data* is empty, or transcription is not configured.
    """
    if not data:
        raise ValueError("audio is required")
    if not is_transcription_enabled():
        raise ValueError("transcription is not configured")

    client = get_transcription_client()
    if not client:
        raise ValueError("transcription is not configured")

    if not _is_wav(data):
        data = _to_wav(data)
        if not data:
            return ""

    # The SDK names the multipart part after the file object, and an unnamed
    # one reaches the backend as a field it refuses.
    upload = io.BytesIO(data)
    upload.name = "voice-message.wav"

    try:
        response = client.audio.transcriptions.create(
            model=settings.AI_ASR_MODEL,
            file=upload,
        )
    except Exception as exc:
        logger.warning(
            "Transcription failed: model=%s bytes=%d error=%s",
            settings.AI_ASR_MODEL,
            len(data),
            scrub(str(exc)),
        )
        return ""

    text = (getattr(response, "text", "") or "").strip()
    logger.info(
        "Voice message transcribed: model=%s bytes=%d chars=%d",
        settings.AI_ASR_MODEL,
        len(data),
        len(text),
    )
    return text

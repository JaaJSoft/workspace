"""Speech-to-text: what a voice message sent to a bot actually says.

The chat model has no ears, so a recording only reaches it as text. That text
is stored on the attachment the first time a reply needs it and replayed from
there afterwards, so each recording is transcribed once per conversation.

Nothing here announces a language. The recognition model identifies it from
the audio, unlike the speech models on the same backend, which reject a
language they do not know rather than falling back to detection.
"""

import io
import logging

from django.conf import settings

from workspace.common.logging import scrub

from ..client import get_transcription_client

logger = logging.getLogger(__name__)


def is_transcription_enabled() -> bool:
    """Whether a recognition backend is configured.

    Deliberately not gated on ``AI_API_KEY``, like the speech side: the
    server has no authentication of its own, and reaching it directly on the
    internal network is a supported deployment.
    """
    return bool(
        settings.AI_ASR_MODEL and (settings.AI_ASR_BASE_URL or settings.AI_BASE_URL)
    )


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

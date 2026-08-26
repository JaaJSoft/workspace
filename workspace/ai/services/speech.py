"""Text-to-speech: turns a bot's written line into the audio of its own voice.

A voice is a reference recording to clone, never a description. The WAV goes
out as ``voice_ref`` alongside the ``reference_text`` transcribing it word for
word, and the output is that speaker: a reference at 233 Hz came back at
231.9 Hz.

Describing the voice instead ("a young woman, soft and composed") is what the
backend's other models do, and it names an archetype rather than a person —
each call samples a new voice matching the description, so a bot changes voice
mid-conversation: 279, 240 then 247 Hz across three messages under one
description. A seed does not fix that either, since it reproduces one exact
call and a chat never sends the same sentence twice. So nothing here describes
a voice, and a bot with no reference cannot speak at all.

The body must reach the model untouched: ``voice_ref`` and ``reference_text``
are not OpenAI fields, and a proxy that strips unknown ones leaves the request
with no speaker, which the backend rejects outright (see ``AI_TTS_BASE_URL``).
"""

import base64
import io
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from openai import APIStatusError

from workspace.common.logging import scrub

from ..client import get_speech_client

logger = logging.getLogger(__name__)

# Which languages may be announced is deployment config (AI_TTS_LANGUAGES):
# the set belongs to the speech model, and naming one it does not know is a
# hard error - qwen3-voicedesign answers 500 on a short code ("fr"), a native
# name ("français") and any language outside its own ten.


def supported_languages() -> frozenset[str]:
    """Languages this deployment's speech model can be told it is reading."""
    return frozenset(
        lang.strip().lower() for lang in settings.AI_TTS_LANGUAGES if lang.strip()
    )


def normalize_language(value: str) -> str:
    """*value* if the speech model knows it, else "" so it detects the language.

    Detection is the answer to anything unrecognized: the backend refuses a
    language it does not know outright, and reading the text is a better
    guess than any this side could substitute.
    """
    language = (value or "").strip().lower()
    return language if language in supported_languages() else ""


# Statuses a later, identical call can still clear: the backend serializes
# every request for a model behind one lock and answers 503 when the wait
# outlives its own timeout.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 503})


class SpeechSynthesisError(RuntimeError):
    """The speech backend returned no audio.

    *rejected* separates a verdict on the request from a backend falling
    over, so the model is told whether to rephrase or to stop trying.
    """

    def __init__(self, message, attempts=1, rejected=False):
        super().__init__(message)
        self.attempts = attempts
        self.rejected = rejected


def is_speech_enabled() -> bool:
    """Whether a speech backend is configured.

    Deliberately not gated on ``AI_API_KEY``: the speech server has no
    authentication of its own, and reaching it directly on the internal
    network is a supported deployment.
    """
    return bool(
        settings.AI_TTS_MODEL and (settings.AI_TTS_BASE_URL or settings.AI_BASE_URL)
    )


@dataclass(frozen=True)
class VoiceReference:
    """A recording of a speaker and the exact words it says.

    The transcript is half of the reference, not documentation of it: the
    model aligns the clone on it, and the two travel together or not at all.
    """

    audio: bytes
    text: str


def default_voice_reference() -> VoiceReference | None:
    """Reference a bot with none of its own speaks through, if configured.

    Nothing else can stand in for it: a bot reaching synthesis without a
    reference has no voice to be given.
    """
    path = (settings.AI_TTS_VOICE_REF or "").strip()
    text = (settings.AI_TTS_VOICE_REF_TEXT or "").strip()
    if not path or not text:
        return None
    try:
        audio = Path(path).read_bytes()
    except OSError as exc:
        logger.warning(
            "Unreadable AI_TTS_VOICE_REF %s: %s", scrub(path), scrub(str(exc))
        )
        return None
    return VoiceReference(audio=audio, text=text) if audio else None


def ai_synthesize_speech(
    text: str,
    reference: VoiceReference,
    language: str = "",
) -> bytes:
    """Speak *text* in the voice *reference* records.

    Args:
        text: What to say, in any language, accented as it should be
              pronounced — the model reads the letters it is given.
        reference: Recording to clone, with the transcript the backend
                   aligns it on.
        language: Which language *text* is in. Anything outside
                  ``AI_TTS_LANGUAGES`` is dropped so the model detects it.

    Returns:
        Raw bytes of the audio, as the backend produced them (WAV today).

    Raises:
        ValueError: If *text* is empty, there is no reference, or speech is
                    not configured.
        SpeechSynthesisError: If every attempt failed.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    if reference is None:
        raise ValueError("a voice reference is required")
    if not is_speech_enabled():
        raise ValueError("speech synthesis is not configured")

    client = get_speech_client()
    if not client:
        raise ValueError("speech synthesis is not configured")

    extra_body = {
        "voice_ref": {
            "type": "base64",
            "data": base64.b64encode(reference.audio).decode(),
        },
        "reference_text": reference.text,
    }
    spoken = normalize_language(language)
    if spoken:
        extra_body["language"] = spoken

    logger.info(
        "Starting speech synthesis: model=%s chars=%d language=%s ref_bytes=%d",
        settings.AI_TTS_MODEL,
        len(text),
        spoken or "auto",
        len(reference.audio),
    )

    audio = _run_with_retry(client, text, extra_body)

    logger.info(
        "Speech synthesized: model=%s bytes=%d seconds=%s",
        settings.AI_TTS_MODEL,
        len(audio),
        audio_duration_seconds(audio),
    )
    return audio


def audio_duration_seconds(data: bytes) -> float | None:
    """Length of *data* in seconds, or None when the header is unreadable.

    Only uncompressed WAV is measured here. Anything else keeps the
    attachment without a duration: the player reads it from the media
    element once it loads, the duration is only needed before that.
    """
    try:
        with wave.open(io.BytesIO(data)) as wav:
            rate = wav.getframerate()
            if not rate:
                return None
            return round(wav.getnframes() / rate, 2)
    except Exception:
        return None


def _post(client, text: str, extra_body: dict) -> bytes:
    # The OpenAI `voice` id names nothing here - the SDK requires the
    # argument, and a proxy in front of the model may reject a request
    # without it.
    response = client.audio.speech.create(
        model=settings.AI_TTS_MODEL,
        input=text,
        voice="",
        extra_body=extra_body,
    )
    return response.content


def _status_of(exc: BaseException) -> int:
    if isinstance(exc, APIStatusError):
        return getattr(exc, "status_code", 0) or 0
    return 0


def _is_retryable(exc: BaseException) -> bool:
    """Whether an identical call still has a chance of succeeding."""
    status = _status_of(exc)
    if not status:
        # A transport error - connect timeout, read timeout, reset.
        return True
    return status in RETRYABLE_STATUSES or status >= 500


def _run_with_retry(client, text: str, extra_body: dict) -> bytes:
    """Call the backend until it returns audio, retrying transient failures."""
    attempts = max(1, settings.AI_TTS_MAX_ATTEMPTS)
    delay = max(0.0, settings.AI_TTS_RETRY_DELAY)

    for attempt in range(1, attempts + 1):
        try:
            audio = _post(client, text, extra_body)
        except Exception as exc:
            failure = exc
        else:
            if audio:
                return audio
            failure = SpeechSynthesisError("the speech model returned no audio")

        rejected = not _is_retryable(failure)
        if attempt >= attempts or rejected:
            break

        logger.warning(
            "Speech attempt %d/%d failed (%s), retrying in %.1fs: text=%.60s",
            attempt,
            attempts,
            scrub(str(failure)),
            delay,
            scrub(text),
        )
        if delay:
            time.sleep(delay)
        delay *= 2

    logger.error(
        "Speech synthesis failed after %d attempt(s): model=%s status=%s "
        "rejected=%s error=%s text=%.60s",
        attempt,
        settings.AI_TTS_MODEL,
        _status_of(failure) or "-",
        rejected,
        scrub(str(failure)),
        scrub(text),
    )
    raise SpeechSynthesisError(
        str(failure), attempts=attempt, rejected=rejected
    ) from failure

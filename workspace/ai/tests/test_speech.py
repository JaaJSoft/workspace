import io
import wave
from unittest.mock import MagicMock, patch

import httpx2
from django.test import SimpleTestCase, override_settings
from openai import APIConnectionError, APIStatusError

from workspace.ai.services.speech import (
    SpeechSynthesisError,
    ai_synthesize_speech,
    audio_duration_seconds,
    is_speech_enabled,
    normalize_language,
)

TTS_SETTINGS = {
    "AI_API_KEY": "test-key",
    "AI_TTS_MODEL": "test-voice-model",
    "AI_TTS_BASE_URL": "https://speech.test/passthrough/v1/",
    "AI_TTS_VOICE": "A default adult voice.",
    "AI_TTS_LANGUAGES": [
        "french",
        "english",
        "spanish",
        "german",
        "italian",
        "portuguese",
        "russian",
        "japanese",
        "korean",
        "chinese",
    ],
    "AI_TTS_MAX_ATTEMPTS": 3,
    "AI_TTS_RETRY_DELAY": 0,
    "AI_TTS_TIMEOUT": 5,
}


def make_wav(seconds=1.0, rate=24000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def _request():
    return httpx2.Request("POST", "https://speech.test/v1/audio/speech")


def _http_error(status_code):
    request = _request()
    response = httpx2.Response(status_code, request=request)
    return APIStatusError("nope", response=response, body=None)


class _ClientPatch:
    """Patch the speech client so each call returns the next queued result."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __enter__(self):
        def create(**kwargs):
            self.calls.append(kwargs)
            outcome = self.results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            response = MagicMock()
            response.content = outcome
            return response

        client = MagicMock()
        client.audio.speech.create.side_effect = create
        self._patcher = patch(
            "workspace.ai.services.speech.get_speech_client", return_value=client
        )
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


@override_settings(**TTS_SETTINGS)
class SynthesizeSpeechTests(SimpleTestCase):
    def test_sends_the_voice_as_a_nested_instruct(self):
        audio = make_wav()
        with _ClientPatch(audio) as http:
            self.assertEqual(
                ai_synthesize_speech("Bonjour Pierre.", "Une jeune femme."), audio
            )

        call = http.calls[0]
        self.assertEqual(call["model"], "test-voice-model")
        self.assertEqual(call["input"], "Bonjour Pierre.")
        # Nested under `options`: at the top level the backend accepts the
        # field and ignores it, which loses the voice without an error.
        self.assertEqual(
            call["extra_body"]["options"], {"instruct": "Une jeune femme."}
        )
        self.assertNotIn("language", call["extra_body"])

    def test_falls_back_to_the_configured_default_voice(self):
        with _ClientPatch(make_wav()) as http:
            ai_synthesize_speech("Hello.", "   ")
        self.assertEqual(
            http.calls[0]["extra_body"]["options"]["instruct"],
            "A default adult voice.",
        )

    def test_sends_the_language_the_caller_named(self):
        with _ClientPatch(make_wav()) as http:
            ai_synthesize_speech("Bonjour.", "Une voix.", "french")
        self.assertEqual(http.calls[0]["extra_body"]["language"], "french")

    def test_omits_the_language_when_the_caller_names_none(self):
        # Nothing is substituted: the backend reads the text, which beats any
        # fixed guess this side could make.
        with _ClientPatch(make_wav()) as http:
            ai_synthesize_speech("Bonjour.", "Une voix.")
        self.assertNotIn("language", http.calls[0]["extra_body"])

    def test_omits_a_language_the_speech_model_does_not_know(self):
        # Never forwarded: an unknown language is a 500, not a soft failure.
        with _ClientPatch(make_wav()) as http:
            ai_synthesize_speech("Goedendag.", "A voice.", "dutch")
        self.assertNotIn("language", http.calls[0]["extra_body"])

    def test_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            ai_synthesize_speech("   ", "A voice.")

    @override_settings(AI_TTS_MODEL="")
    def test_rejects_an_unconfigured_backend(self):
        with self.assertRaises(ValueError):
            ai_synthesize_speech("Hello.", "A voice.")


@override_settings(**TTS_SETTINGS)
class SynthesizeSpeechRetryTests(SimpleTestCase):
    def test_retries_a_busy_backend(self):
        # 503 server_busy is what the backend answers when another request
        # held the model lock for too long - the next call usually gets it.
        audio = make_wav()
        with _ClientPatch(_http_error(503), audio) as http:
            self.assertEqual(ai_synthesize_speech("Hello.", "A voice."), audio)
        self.assertEqual(len(http.calls), 2)

    def test_retries_a_response_carrying_no_audio(self):
        audio = make_wav()
        with _ClientPatch(b"", audio) as http:
            self.assertEqual(ai_synthesize_speech("Hello.", "A voice."), audio)
        self.assertEqual(len(http.calls), 2)

    def test_gives_up_after_the_configured_attempts(self):
        with _ClientPatch(*[_http_error(500)] * 3) as http:
            with self.assertRaises(SpeechSynthesisError) as caught:
                ai_synthesize_speech("Hello.", "A voice.")
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(caught.exception.attempts, 3)
        self.assertFalse(caught.exception.rejected)

    def test_does_not_retry_a_rejected_request(self):
        with _ClientPatch(_http_error(400)) as http:
            with self.assertRaises(SpeechSynthesisError) as caught:
                ai_synthesize_speech("Hello.", "A voice.")
        self.assertEqual(len(http.calls), 1)
        self.assertTrue(caught.exception.rejected)

    def test_retries_a_transport_failure(self):
        audio = make_wav()
        with _ClientPatch(APIConnectionError(request=_request()), audio) as http:
            self.assertEqual(ai_synthesize_speech("Hello.", "A voice."), audio)
        self.assertEqual(len(http.calls), 2)


@override_settings(**TTS_SETTINGS)
class LanguageNormalizationTests(SimpleTestCase):
    def test_a_configured_language_passes_through_any_case(self):
        self.assertEqual(normalize_language("french"), "french")
        self.assertEqual(normalize_language("  Japanese "), "japanese")

    def test_anything_the_speech_model_rejects_becomes_detection(self):
        # Measured against the backend: each of these is a 500 there.
        for value in ("fr", "en", "français", "dutch", "polish", "", "  ", None):
            self.assertEqual(normalize_language(value), "", repr(value))

    @override_settings(AI_TTS_LANGUAGES=["dutch", "polish"])
    def test_the_allowed_set_follows_the_configuration(self):
        # Another speech model takes another vocabulary, so what may be
        # announced is deployment config, not a constant of this module.
        self.assertEqual(normalize_language("dutch"), "dutch")
        self.assertEqual(normalize_language("french"), "")


class AudioDurationTests(SimpleTestCase):
    def test_reads_the_duration_from_the_wav_header(self):
        self.assertEqual(audio_duration_seconds(make_wav(seconds=2.5)), 2.5)

    def test_returns_none_for_unreadable_bytes(self):
        self.assertIsNone(audio_duration_seconds(b"not audio at all"))


@override_settings(**TTS_SETTINGS)
class SpeechEnabledTests(SimpleTestCase):
    def test_enabled_with_a_model_and_an_endpoint(self):
        self.assertTrue(is_speech_enabled())

    @override_settings(AI_TTS_MODEL="")
    def test_disabled_without_a_model(self):
        self.assertFalse(is_speech_enabled())

    @override_settings(AI_TTS_BASE_URL=None, AI_BASE_URL=None)
    def test_disabled_without_an_endpoint(self):
        self.assertFalse(is_speech_enabled())

    @override_settings(AI_API_KEY="")
    def test_enabled_without_a_key(self):
        # The speech server has no authentication of its own; requiring a
        # key here would lock out reaching it directly on the internal net.
        self.assertTrue(is_speech_enabled())

    @override_settings(AI_API_KEY="")
    def test_a_client_is_still_built_without_a_key(self):
        # The speech server has no authentication of its own, and the SDK
        # refuses to construct without a key - so one is substituted rather
        # than the whole feature being disabled.
        from workspace.ai.client import get_speech_client

        self.assertIsNotNone(get_speech_client())

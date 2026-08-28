from unittest.mock import MagicMock, patch

import httpx2
from django.test import SimpleTestCase, override_settings
from openai import APIStatusError

from workspace.ai.services.transcription import (
    ai_transcribe_audio,
    is_transcription_enabled,
)

from .test_speech import make_wav

ASR_SETTINGS = {
    "AI_API_KEY": "test-key",
    "AI_ASR_MODEL": "test-listen-model",
    "AI_ASR_BASE_URL": "https://speech.test/passthrough/v1/",
    "AI_ASR_TIMEOUT": 5,
}

RECORDING = make_wav()


def _http_error(status_code):
    request = httpx2.Request("POST", "https://speech.test/v1/audio/transcriptions")
    response = httpx2.Response(status_code, request=request)
    return APIStatusError("nope", response=response, body=None)


class _ClientPatch:
    """Patch the transcription client so each call returns the next result."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __enter__(self):
        def create(**kwargs):
            self.calls.append(kwargs)
            outcome = self.results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return MagicMock(text=outcome)

        client = MagicMock()
        client.audio.transcriptions.create.side_effect = create
        self._patcher = patch(
            "workspace.ai.services.transcription.get_transcription_client",
            return_value=client,
        )
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


@override_settings(**ASR_SETTINGS)
class TranscribeAudioTests(SimpleTestCase):
    def test_returns_what_the_backend_heard(self):
        with _ClientPatch("  Bonjour Pierre, tu es la ?  "):
            self.assertEqual(
                ai_transcribe_audio(RECORDING), "Bonjour Pierre, tu es la ?"
            )

    def test_sends_the_configured_model(self):
        with _ClientPatch("Bonjour.") as http:
            ai_transcribe_audio(RECORDING)

        self.assertEqual(http.calls[0]["model"], "test-listen-model")

    def test_the_upload_carries_a_filename(self):
        # The SDK builds the multipart part from the file object's name;
        # without one the backend receives an unnamed field and rejects it.
        with _ClientPatch("Bonjour.") as http:
            ai_transcribe_audio(RECORDING)

        sent = http.calls[0]["file"]
        self.assertTrue(getattr(sent, "name", ""))
        self.assertEqual(sent.read(), RECORDING)

    def test_announces_no_language(self):
        # The model identifies it on its own, and naming one it does not
        # know is how the speech side of this backend answers 500.
        with _ClientPatch("Bonjour.") as http:
            ai_transcribe_audio(RECORDING)

        self.assertNotIn("language", http.calls[0])

    def test_a_backend_failure_reads_as_nothing_heard(self):
        # The caller keeps its "could not listen" note and tries again on the
        # next turn, so a failure must not take the whole reply down.
        with _ClientPatch(_http_error(503)):
            self.assertEqual(ai_transcribe_audio(RECORDING), "")

    def test_silence_reads_as_nothing_heard(self):
        with _ClientPatch("   "):
            self.assertEqual(ai_transcribe_audio(RECORDING), "")

    def test_rejects_empty_audio(self):
        with self.assertRaises(ValueError):
            ai_transcribe_audio(b"")

    @override_settings(AI_ASR_MODEL="")
    def test_rejects_an_unconfigured_backend(self):
        with self.assertRaises(ValueError):
            ai_transcribe_audio(RECORDING)


@override_settings(**ASR_SETTINGS)
class TranscriptionEnabledTests(SimpleTestCase):
    def test_enabled_with_a_model_and_an_endpoint(self):
        self.assertTrue(is_transcription_enabled())

    @override_settings(AI_ASR_MODEL="")
    def test_disabled_without_a_model(self):
        self.assertFalse(is_transcription_enabled())

    @override_settings(AI_ASR_BASE_URL=None, AI_BASE_URL=None)
    def test_disabled_without_an_endpoint(self):
        self.assertFalse(is_transcription_enabled())

    @override_settings(AI_API_KEY="")
    def test_enabled_without_a_key(self):
        # Same deployment as the speech server: no authentication of its own.
        self.assertTrue(is_transcription_enabled())

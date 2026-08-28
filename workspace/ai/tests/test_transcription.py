import subprocess
from unittest.mock import MagicMock, patch

import httpx2
from django.test import SimpleTestCase, override_settings
from openai import APIStatusError

from workspace.ai.services import transcription
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


# What a browser actually uploads: MediaRecorder offers webm/opus and m4a,
# never WAV, and this backend takes WAV only ("only WAV audio uploads are
# currently supported for transcription").
WEBM = bytes([0x1A, 0x45, 0xDF, 0xA3]) + bytes(64)  # EBML header


@override_settings(**ASR_SETTINGS)
class TranscodingTests(SimpleTestCase):
    def test_a_wav_recording_is_sent_untouched(self):
        with patch.object(transcription, "_to_wav") as convert:
            with _ClientPatch("Bonjour.") as http:
                ai_transcribe_audio(RECORDING)

        convert.assert_not_called()
        self.assertEqual(http.calls[0]["file"].read(), RECORDING)

    def test_a_browser_recording_is_transcoded_before_it_is_sent(self):
        with patch.object(transcription, "_to_wav", return_value=RECORDING) as convert:
            with _ClientPatch("Bonjour.") as http:
                self.assertEqual(ai_transcribe_audio(WEBM), "Bonjour.")

        convert.assert_called_once_with(WEBM)
        self.assertEqual(http.calls[0]["file"].read(), RECORDING)

    def test_nothing_is_sent_when_the_recording_cannot_be_transcoded(self):
        # ffmpeg missing or refusing the container: the caller keeps its
        # "could not listen" note rather than the backend answering 400.
        with patch.object(transcription, "_to_wav", return_value=None):
            with _ClientPatch("Bonjour.") as http:
                self.assertEqual(ai_transcribe_audio(WEBM), "")

        self.assertEqual(http.calls, [])

    def test_transcoding_is_skipped_without_ffmpeg(self):
        with patch.object(transcription, "_FFMPEG", None):
            self.assertIsNone(transcription._to_wav(WEBM))

    @override_settings(CHAT_VOICE_MAX_SECONDS=300)
    def test_the_decode_stops_at_the_length_the_recorder_allows(self):
        # The upload cap is 50 MB, and Opus at its lowest bitrate packs about
        # nineteen hours into that - two gigabytes of PCM read into a worker.
        with (
            patch.object(transcription, "_FFMPEG", "/usr/bin/ffmpeg"),
            patch.object(transcription.subprocess, "run") as run,
        ):
            transcription._to_wav(WEBM)

        args = run.call_args[0][0]
        self.assertIn("-t", args)
        self.assertEqual(args[args.index("-t") + 1], "300")
        # Before -i, so an overlong recording is never decoded in the first
        # place rather than decoded whole and trimmed afterwards.
        self.assertLess(args.index("-t"), args.index("-i"))

    @override_settings(CHAT_VOICE_MAX_SECONDS=1)
    def test_a_conversion_that_overruns_its_budget_is_refused(self):
        # Defence for the case where the cap above did not hold: the refusal
        # happens before the file is read, which is where the memory goes.
        oversized = b"\x00" * (1 * 16000 * 2 + 100_000)

        def fake_run(args, **kwargs):
            with open(args[-1], "wb") as out:
                out.write(oversized)
            return MagicMock(returncode=0)

        with (
            patch.object(transcription, "_FFMPEG", "/usr/bin/ffmpeg"),
            patch.object(transcription.subprocess, "run", side_effect=fake_run),
        ):
            self.assertIsNone(transcription._to_wav(WEBM))

    @override_settings(CHAT_VOICE_MAX_SECONDS=5)
    def test_a_conversion_within_its_budget_is_returned(self):
        def fake_run(args, **kwargs):
            with open(args[-1], "wb") as out:
                out.write(RECORDING)
            return MagicMock(returncode=0)

        with (
            patch.object(transcription, "_FFMPEG", "/usr/bin/ffmpeg"),
            patch.object(transcription.subprocess, "run", side_effect=fake_run),
        ):
            self.assertEqual(transcription._to_wav(WEBM), RECORDING)

    def test_a_failing_ffmpeg_yields_no_audio(self):
        error = subprocess.CalledProcessError(1, "ffmpeg")
        with (
            patch.object(transcription, "_FFMPEG", "/usr/bin/ffmpeg"),
            patch.object(transcription.subprocess, "run", side_effect=error),
        ):
            self.assertIsNone(transcription._to_wav(WEBM))


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

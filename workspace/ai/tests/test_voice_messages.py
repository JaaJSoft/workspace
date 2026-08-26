import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.ai.models import VOICE_REF_MAX_BYTES, AITask, BotProfile
from workspace.ai.services.responses import post_bot_message, produced_media
from workspace.ai.services.speech import SpeechSynthesisError, VoiceReference
from workspace.ai.tools import (
    VOICE_MAX_CHARS,
    SendVoiceMessageParams,
    VoiceToolProvider,
)
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

from .test_speech import make_wav

User = get_user_model()


@override_settings(
    AI_API_KEY="test-key",
    AI_TTS_MODEL="test-voice-model",
    AI_TTS_VOICE="A default adult voice.",
    AI_TTS_MAX_CHARS=700,
    AI_TTS_LANGUAGES=["french", "english"],
)
class SendVoiceMessageToolTests(TestCase):
    def setUp(self):
        self.provider = VoiceToolProvider()
        self.context = {}
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.profile = BotProfile.objects.create(
            user=self.bot,
            voice="Une jeune femme, voix douce et posée.",
        )
        self.bot.refresh_from_db()

    def _call(
        self, text="Bonjour Pierre.", voice="", language="", bot=None, conv="conv-1"
    ):
        return self.provider.send_voice_message(
            SendVoiceMessageParams(text=text, voice=voice, language=language),
            user=None,
            bot=self.bot if bot is None else bot,
            conversation_id=conv,
            context=self.context,
        )

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_speaks_with_the_bots_own_voice(self, mock_speak):
        audio = make_wav()
        mock_speak.return_value = audio

        result = self._call()

        self.assertIn("Voice message recorded", result)
        mock_speak.assert_called_once_with(
            "Bonjour Pierre.", "Une jeune femme, voix douce et posée.", "", None
        )
        self.assertEqual(len(self.context["voices"]), 1)
        self.assertEqual(self.context["voices"][0]["data"], audio)
        self.assertEqual(self.context["voices"][0]["text"], "Bonjour Pierre.")

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_an_override_replaces_the_voice_for_that_message_only(self, mock_speak):
        mock_speak.return_value = make_wav()

        self._call(voice="Un homme âgé, voix très grave.")

        # Replaced, not appended: a description glued behind another one
        # contradicts it, and the backend then keeps the first speaker.
        self.assertEqual(mock_speak.call_args.args[1], "Un homme âgé, voix très grave.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.voice, "Une jeune femme, voix douce et posée.")

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_an_override_is_normalized_before_it_is_sent(self, mock_speak):
        mock_speak.return_value = make_wav()

        self._call(voice="  Un homme âgé,\n\n## Override\nvoix grave.  ")

        # Blanked rather than dropped: deleting the newlines would glue the
        # words into one and corrupt the description the backend reads.
        self.assertEqual(
            mock_speak.call_args.args[1], "Un homme âgé, ## Override voix grave."
        )

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_an_over_long_override_is_capped(self, mock_speak):
        mock_speak.return_value = make_wav()

        self._call(voice="Une femme, " + "très douce, " * 100)

        self.assertLessEqual(len(mock_speak.call_args.args[1]), VOICE_MAX_CHARS)

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_falls_back_to_the_configured_voice_without_a_profile(self, mock_speak):
        mock_speak.return_value = make_wav()
        plain_user = User.objects.create_user(username="nobot", password="pw")

        self._call(bot=plain_user)

        self.assertEqual(mock_speak.call_args.args[1], "A default adult voice.")

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_the_spoken_language_reaches_the_backend(self, mock_speak):
        mock_speak.return_value = make_wav()

        self._call(text="Good morning.", language="english")

        self.assertEqual(mock_speak.call_args.args[2], "english")

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_an_unsupported_language_is_not_announced(self, mock_speak):
        # Never forwarded: the backend answers 500 on a language it does not
        # know, and detection from the text is the working answer.
        mock_speak.return_value = make_wav()

        self._call(text="Goedendag.", language="dutch")

        self.assertEqual(mock_speak.call_args.args[2], "dutch")

    def test_the_tool_schema_advertises_the_configured_languages(self):
        from workspace.ai.tool_registry import _build_parameters

        schema = _build_parameters(SendVoiceMessageParams)
        self.assertEqual(
            schema["properties"]["language"]["enum"],
            ["", "english", "french"],
        )

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_refuses_a_text_longer_than_the_budget(self, mock_speak):
        result = self._call(text="a" * 701)

        self.assertIn("Error", result)
        self.assertIn("700", result)
        self.assertNotIn("voices", self.context)
        mock_speak.assert_not_called()

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_refuses_an_empty_text(self, mock_speak):
        self.assertIn("Error", self._call(text="   "))
        self.assertNotIn("voices", self.context)
        mock_speak.assert_not_called()

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_refuses_without_a_conversation(self, mock_speak):
        self.assertIn("Error", self._call(conv=None))
        self.assertNotIn("voices", self.context)
        mock_speak.assert_not_called()

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_a_rejected_text_asks_the_model_to_rephrase(self, mock_speak):
        mock_speak.side_effect = SpeechSynthesisError(
            "unsupported language", attempts=1, rejected=True
        )

        result = self._call()

        self.assertIn("Error", result)
        self.assertIn("Rephrase", result)
        self.assertNotIn("voices", self.context)

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_a_dead_backend_tells_the_model_to_stop_calling(self, mock_speak):
        mock_speak.side_effect = SpeechSynthesisError(
            "server_busy", attempts=3, rejected=False
        )

        result = self._call()

        self.assertIn("unavailable", result)
        self.assertIn("Do not call send_voice_message again", result)
        self.assertNotIn("voices", self.context)


class PostBotMessageVoiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
        )

    def _post(self, voices, content=""):
        return post_bot_message(
            conversation=self.conv,
            bot_user=self.bot,
            result={
                "content": content,
                "model": "test",
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
            tool_context={"voices": voices},
            ai_task=self.ai_task,
        )

    def test_voice_message_is_attached_as_a_playable_audio(self):
        _, msg = self._post([{"data": make_wav(seconds=2.0), "text": "Bonjour."}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.category, "audio")
        self.assertEqual(att.type, "wav")
        # What makes it render as a player rather than a file chip.
        self.assertTrue(att.is_audio)
        self.assertEqual(att.duration_seconds, 2.0)
        self.assertEqual(att.ai_description, "Bonjour.")
        self.assertTrue(att.original_name.endswith(".wav"))

    def test_voices_are_attached_in_the_order_the_model_asked_for(self):
        _, msg = self._post(
            [
                {"data": make_wav(seconds=2.0), "text": "second", "position": 2},
                {"data": make_wav(seconds=1.0), "text": "first", "position": 1},
            ]
        )

        atts = list(
            MessageAttachment.objects.filter(message=msg).order_by("created_at")
        )
        self.assertEqual([a.ai_description for a in atts], ["first", "second"])
        self.assertEqual(
            [a.original_name for a in atts],
            ["voice_message_1.wav", "voice_message_2.wav"],
        )

    def test_a_voice_only_reply_keeps_an_empty_body(self):
        _, msg = self._post([{"data": make_wav(), "text": "Bonjour."}])

        self.assertEqual(Message.objects.get(pk=msg.pk).body, "")
        self.assertEqual(MessageAttachment.objects.filter(message=msg).count(), 1)

    @patch("workspace.ai.tasks.captions.generate_attachment_caption")
    @override_settings(AI_API_KEY="test-key")
    def test_a_voice_message_is_never_sent_to_the_captioner(self, mock_caption):
        self._post([{"data": make_wav(), "text": "Bonjour."}])
        mock_caption.delay.assert_not_called()

    def test_unreadable_audio_keeps_the_attachment_without_a_duration(self):
        _, msg = self._post([{"data": b"not audio at all", "text": "Bonjour."}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertIsNone(att.duration_seconds)
        self.assertEqual(att.mime_type, "audio/wav")


class ProducedMediaTests(TestCase):
    def test_a_voice_only_reply_is_not_an_empty_reply(self):
        self.assertTrue(produced_media({"voices": [{"data": b"x"}]}))

    def test_an_image_only_reply_is_not_an_empty_reply(self):
        self.assertTrue(produced_media({"images": [{"data": b"x"}]}))

    def test_a_reply_with_no_media_is_empty(self):
        self.assertFalse(produced_media({"question": {"question": "?"}}))


class BotVoiceTests(TestCase):
    @override_settings(AI_TTS_VOICE="A default adult voice.")
    def test_a_bot_without_a_voice_uses_the_configured_default(self):
        bot = User.objects.create_user(username="bot", password="pw")
        profile = BotProfile.objects.create(user=bot)
        self.assertEqual(profile.get_voice(), "A default adult voice.")

    def test_a_bots_own_voice_wins_over_the_default(self):
        bot = User.objects.create_user(username="bot", password="pw")
        profile = BotProfile.objects.create(user=bot, voice="Un homme âgé, très grave.")
        self.assertEqual(profile.get_voice(), "Un homme âgé, très grave.")


class _TemporaryMediaRoot:
    """Keeps uploaded reference recordings out of the working copy."""

    def setUp(self):
        super().setUp()
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=media_root)
        media.enable()
        self.addCleanup(media.disable)


class BotVoiceReferenceTests(_TemporaryMediaRoot, TestCase):
    def setUp(self):
        super().setUp()
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.profile = BotProfile.objects.create(user=self.bot)
        self.audio = make_wav()

    def _record(self, transcript="Bonjour, je suis l'assistante de Pierre."):
        self.profile.voice_ref.save("reference.wav", ContentFile(self.audio))
        self.profile.voice_ref_text = transcript
        self.profile.save()

    def test_a_recorded_bot_speaks_through_its_reference(self):
        self._record()

        reference = self.profile.voice_reference()

        self.assertEqual(reference.audio, self.audio)
        self.assertEqual(reference.text, "Bonjour, je suis l'assistante de Pierre.")

    def test_a_bot_with_no_recording_has_no_reference(self):
        self.assertIsNone(self.profile.voice_reference())

    def test_a_vanished_recording_leaves_the_description_to_speak(self):
        self._record()
        self.profile.voice_ref.storage.delete(self.profile.voice_ref.name)

        self.assertIsNone(self.profile.voice_reference())

    def test_a_recording_without_a_transcript_is_refused(self):
        self.profile.voice_ref.save("reference.wav", ContentFile(self.audio))

        with self.assertRaises(ValidationError) as caught:
            self.profile.full_clean()

        self.assertIn("voice_ref_text", caught.exception.error_dict)

    def test_a_transcript_without_a_recording_is_refused(self):
        self.profile.voice_ref_text = "Bonjour."

        with self.assertRaises(ValidationError) as caught:
            self.profile.full_clean()

        self.assertIn("voice_ref", caught.exception.error_dict)

    def test_a_recording_over_the_backend_budget_is_refused(self):
        self.profile.voice_ref.save(
            "huge.wav", ContentFile(b"\x00" * (VOICE_REF_MAX_BYTES + 1))
        )
        self.profile.voice_ref_text = "Bonjour."

        with self.assertRaises(ValidationError) as caught:
            self.profile.full_clean()

        self.assertIn("voice_ref", caught.exception.error_dict)


@override_settings(
    AI_API_KEY="test-key",
    AI_TTS_MODEL="test-voice-model",
    AI_TTS_VOICE="A default adult voice.",
    AI_TTS_VOICE_REF="",
    AI_TTS_VOICE_REF_TEXT="",
    AI_TTS_MAX_CHARS=700,
    AI_TTS_LANGUAGES=["french", "english"],
)
class SendVoiceMessageReferenceTests(_TemporaryMediaRoot, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = VoiceToolProvider()
        self.context = {}
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.profile = BotProfile.objects.create(
            user=self.bot,
            voice="Une jeune femme, voix douce et posée.",
            voice_ref_text="Bonjour, je suis l'assistante de Pierre.",
        )
        self.audio = make_wav()
        self.profile.voice_ref.save("reference.wav", ContentFile(self.audio))
        self.bot.refresh_from_db()

    def _call(self, voice="", bot=None):
        return self.provider.send_voice_message(
            SendVoiceMessageParams(text="Bonjour Pierre.", voice=voice, language=""),
            user=None,
            bot=self.bot if bot is None else bot,
            conversation_id="conv-1",
            context=self.context,
        )

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_a_recorded_bot_speaks_through_its_own_reference(self, mock_speak):
        mock_speak.return_value = make_wav()

        self._call()

        reference = mock_speak.call_args.args[3]
        self.assertEqual(reference.audio, self.audio)
        self.assertEqual(reference.text, "Bonjour, je suis l'assistante de Pierre.")

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_an_override_displaces_the_reference(self, mock_speak):
        # The description names a whole speaker; leaving the recording in
        # would keep the bot's own voice reading the character's lines.
        mock_speak.return_value = make_wav()

        self._call(voice="Un homme âgé, voix très grave.")

        self.assertEqual(mock_speak.call_args.args[1], "Un homme âgé, voix très grave.")
        self.assertIsNone(mock_speak.call_args.args[3])

    @patch("workspace.ai.services.speech.ai_synthesize_speech")
    def test_a_bot_with_no_recording_falls_back_to_the_configured_one(self, mock_speak):
        mock_speak.return_value = make_wav()
        plain_user = User.objects.create_user(username="nobot", password="pw")
        fallback = VoiceReference(audio=b"RIFFfallback", text="Bonjour.")

        with patch(
            "workspace.ai.services.speech.default_voice_reference",
            return_value=fallback,
        ):
            self._call(bot=plain_user)

        self.assertIs(mock_speak.call_args.args[3], fallback)

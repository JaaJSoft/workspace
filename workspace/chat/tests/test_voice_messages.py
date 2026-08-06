from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, MessageAttachment
from workspace.files.models import File

User = get_user_model()

# Minimal EBML header: enough for Magika to recognise a WebM container.
WEBM_HEADER = (
    b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1f"
    b"\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08"
    b"\x42\x82\x84webm\x42\x87\x81\x02\x42\x85\x81\x02"
) + b"\x00" * 512


class VoiceMessageIngestTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.client.force_authenticate(self.user)
        self.url = f"/api/v1/chat/conversations/{self.conv.pk}/messages"

    def _webm(self, content_type):
        return SimpleUploadedFile("voice.webm", WEBM_HEADER, content_type=content_type)

    def test_audio_only_webm_pins_the_audio_viewer(self):
        resp = self.client.post(
            self.url,
            {"files": self._webm("audio/webm;codecs=opus"), "duration": "12.5"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        att = MessageAttachment.objects.get()
        self.assertEqual(att.viewer, "audio")
        self.assertTrue(att.is_audio)
        self.assertAlmostEqual(att.duration_seconds, 12.5)

    def test_detection_is_left_untouched(self):
        """The pin is a display decision. Magika read a WebM container and it
        stays a WebM container - this assertion is what proves we did not bend
        the detector to fix a rendering problem."""
        self.client.post(
            self.url,
            {"files": self._webm("audio/webm"), "duration": "3"},
            format="multipart",
        )
        att = MessageAttachment.objects.get()
        self.assertEqual(att.category, "video")
        self.assertEqual(att.mime_type, "video/webm")
        self.assertEqual(att.type, "webm")

    def test_webm_declared_video_is_not_pinned(self):
        resp = self.client.post(
            self.url, {"files": self._webm("video/webm")}, format="multipart"
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        att = MessageAttachment.objects.get()
        self.assertEqual(att.viewer, "")
        self.assertFalse(att.is_audio)
        self.assertIsNone(att.duration_seconds)

    def test_duration_requires_exactly_one_file(self):
        resp = self.client.post(
            self.url,
            {
                "files": [self._webm("audio/webm"), self._webm("audio/webm")],
                "duration": "5",
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_duration_rejected_on_a_non_audio_file(self):
        resp = self.client.post(
            self.url,
            {
                "files": SimpleUploadedFile(
                    "note.txt", b"hello world\n" * 40, content_type="text/plain"
                ),
                "duration": "5",
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    @override_settings(CHAT_VOICE_MAX_SECONDS=10)
    def test_duration_over_the_limit_is_rejected(self):
        resp = self.client.post(
            self.url,
            {"files": self._webm("audio/webm"), "duration": "11"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_zero_duration_is_rejected(self):
        resp = self.client.post(
            self.url,
            {"files": self._webm("audio/webm"), "duration": "0"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_audio_without_duration_is_accepted(self):
        """A shared file carries no client-measured duration; the browser reads
        it from the container header."""
        resp = self.client.post(
            self.url, {"files": self._webm("audio/webm")}, format="multipart"
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(MessageAttachment.objects.get().duration_seconds)

    def test_serializer_exposes_the_audio_fields(self):
        self.client.post(
            self.url,
            {"files": self._webm("audio/webm"), "duration": "8"},
            format="multipart",
        )
        resp = self.client.get(self.url)
        att = resp.data["messages"][0]["attachments"][0]
        self.assertTrue(att["is_audio"])
        self.assertAlmostEqual(att["duration_seconds"], 8.0)


class VoiceMessageViewerTests(APITestCase):
    """view_attachment must honour the pin, not the detected category."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="bob", email="bob@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.client.force_authenticate(self.user)
        self.client.force_login(self.user)
        self.url = f"/api/v1/chat/conversations/{self.conv.pk}/messages"

    def _send_voice(self):
        self.client.post(
            self.url,
            {
                "files": SimpleUploadedFile(
                    "voice.webm", WEBM_HEADER, content_type="audio/webm"
                ),
                "duration": "4",
            },
            format="multipart",
        )
        return MessageAttachment.objects.get()

    def test_modal_renders_an_audio_element(self):
        att = self._send_voice()
        resp = self.client.get(
            reverse("chat_ui:view_attachment", kwargs={"attachment_uuid": att.uuid})
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("<audio", html)
        self.assertNotIn("<video", html)

    def test_an_unknown_pin_falls_back_to_content_resolution(self):
        """A slug left over from a removed viewer must not 500 the modal."""
        att = self._send_voice()
        att.viewer = "no-such-viewer"
        att.save(update_fields=["viewer"])
        resp = self.client.get(
            reverse("chat_ui:view_attachment", kwargs={"attachment_uuid": att.uuid})
        )
        self.assertEqual(resp.status_code, 200)


class VoiceMessageSaveToFilesTests(APITestCase):
    """Saving a voice message to Files must keep its audio identity.

    The stored mime type is the detected container (video/webm), so the pin
    cannot be re-derived on the way in - it has to travel with the row.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="carol", email="carol@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.client.force_authenticate(self.user)

    def _send(self, content_type, duration=None):
        payload = {
            "files": SimpleUploadedFile(
                "voice.webm", WEBM_HEADER, content_type=content_type
            )
        }
        if duration is not None:
            payload["duration"] = duration
        self.client.post(
            f"/api/v1/chat/conversations/{self.conv.pk}/messages",
            payload,
            format="multipart",
        )
        return MessageAttachment.objects.get()

    def _save_to_files(self, att):
        resp = self.client.post(
            f"/api/v1/chat/attachments/{att.uuid}/save-to-files", {}, format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        return File.objects.get(uuid=resp.data["file_uuid"])

    def test_saving_a_voice_message_keeps_the_audio_pin(self):
        att = self._send("audio/webm", duration="6")
        self.assertEqual(att.viewer, "audio")

        saved = self._save_to_files(att)

        self.assertEqual(saved.viewer, "audio")
        # The pin is a display decision: detection stays what it was.
        self.assertEqual(saved.category, "video")

    def test_saving_a_video_attachment_pins_nothing(self):
        att = self._send("video/webm")
        saved = self._save_to_files(att)
        self.assertEqual(saved.viewer, "")

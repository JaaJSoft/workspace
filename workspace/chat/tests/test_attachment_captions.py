from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()

PNG_DETECTION = SimpleNamespace(mime_type="image/png", label="png", group="image")


class UploadCaptionEnqueueTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="uploader", email="up@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.client.force_authenticate(self.user)
        self.url = f"/api/v1/chat/conversations/{self.conv.pk}/messages"

    @override_settings(AI_API_KEY="k")
    def test_image_upload_enqueues_caption(self):
        upload = SimpleUploadedFile("photo.png", b"fakepng", content_type="image/png")
        with (
            patch(
                "workspace.files.services.detection.detect_from_stream",
                return_value=PNG_DETECTION,
            ),
            patch(
                "workspace.ai.tasks.captions.generate_attachment_caption.delay"
            ) as mock_delay,
        ):
            resp = self.client.post(
                self.url, {"body": "here", "files": [upload]}, format="multipart"
            )
        self.assertEqual(resp.status_code, 201, resp.content)
        mock_delay.assert_called_once()

    @override_settings(AI_API_KEY="k")
    def test_text_only_message_enqueues_nothing(self):
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            resp = self.client.post(self.url, {"body": "hi"}, format="multipart")
        self.assertEqual(resp.status_code, 201, resp.content)
        mock_delay.assert_not_called()

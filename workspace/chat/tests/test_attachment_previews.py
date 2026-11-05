"""GET /chat/attachment-previews: chat attachments as <file-preview> slots.

The preview is the one the Files viewers draw (files/ui/viewers.py); chat
only decides who may see an attachment - an active member of its
conversation - and where its bytes come from."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, MessageAttachment

User = get_user_model()


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class AttachmentPreviewsTests(APITestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="pv_member", password="pw")
        self.outsider = User.objects.create_user(username="pv_outsider", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.member
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.member)
        self.client.force_login(self.member)
        self.client.post(
            f"/api/v1/chat/conversations/{self.conv.pk}/messages",
            {
                "body": "",
                "files": [
                    SimpleUploadedFile("shot.png", _png(), content_type="image/png"),
                    SimpleUploadedFile(
                        "bundle.zip", b"PK\x03\x04", content_type="application/zip"
                    ),
                ],
            },
            format="multipart",
        )
        self.image = MessageAttachment.objects.get(original_name="shot.png")
        self.archive = MessageAttachment.objects.get(original_name="bundle.zip")

    def _get(self, *pairs):
        return self.client.get(
            reverse("chat_ui:attachment_previews"),
            {
                "uuids": ",".join(str(att.uuid) for _, att in pairs),
                "targets": ",".join(target for target, _ in pairs),
            },
        )

    def test_member_gets_each_preview_in_its_slot(self):
        response = self._get(("fp-1", self.image), ("fp-2", self.archive))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<div id="fp-1" class="file-preview-slot">', html)
        self.assertIn("file-preview-image", html)
        self.assertIn(f'src="/api/v1/chat/attachments/{self.image.uuid}"', html)
        self.assertIn('<div id="fp-2" class="file-preview-slot">', html)
        self.assertIn("file-preview-card", html)
        self.assertIn("bundle.zip", html)

    def test_outsider_gets_nothing(self):
        self.client.force_login(self.outsider)
        response = self._get(("fp-1", self.image))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_former_member_gets_nothing(self):
        from django.utils import timezone

        ConversationMember.objects.filter(
            conversation=self.conv, user=self.member
        ).update(left_at=timezone.now())
        self.assertEqual(self._get(("fp-1", self.image)).content, b"")

    def test_malformed_batch_is_refused(self):
        response = self.client.get(
            reverse("chat_ui:attachment_previews"), {"uuids": "x", "targets": "fp-1"}
        )
        self.assertEqual(response.status_code, 400)

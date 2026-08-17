"""The attachment viewer endpoint must wrap its response in the #viewer-panel
anchor: the chat attachment modal loads it through alpine-ajax, which merges
by element id, so a body without the anchor never renders."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, MessageAttachment
from workspace.files.ui.viewers import VIEWER_PANEL_ID

User = get_user_model()


class AttachmentViewerPanelTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="panel_user", email="panel@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.client.force_login(self.user)

    def test_the_viewer_response_is_wrapped_in_the_panel(self):
        self.client.post(
            f"/api/v1/chat/conversations/{self.conv.pk}/messages",
            {
                "body": "",
                "files": SimpleUploadedFile(
                    "note.txt", b"hello\n" * 40, content_type="text/plain"
                ),
            },
            format="multipart",
        )
        att = MessageAttachment.objects.get()
        resp = self.client.get(
            reverse("chat_ui:view_attachment", kwargs={"attachment_uuid": att.uuid})
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertTrue(html.startswith(f'<div id="{VIEWER_PANEL_ID}"'), html[:120])

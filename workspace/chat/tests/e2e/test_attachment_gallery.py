"""E2E: the attachment viewer pages through the attachments of the surface it
was opened from - the main flow or the thread panel.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class AttachmentGalleryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="gallery-user")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.root = Message.objects.create(
            conversation=self.conv,
            author=self.user,
            body="look at these",
            body_html=render_message_body("look at these"),
            reply_count=1,
        )

        client = Client()
        client.force_login(self.user)
        resp = client.post(
            f"/api/v1/chat/conversations/{self.conv.uuid}/messages",
            {
                "body": "two pictures",
                "files": [
                    SimpleUploadedFile(name, PNG_1PX, content_type="image/png")
                    for name in ("one.png", "two.png")
                ],
            },
        )
        assert resp.status_code == 201, resp.content
        Message.objects.filter(body="two pictures").update(
            thread_root=self.root, reply_to=self.root
        )

    def test_an_attachment_opened_in_a_thread_pages_through_the_thread(self):
        self.login_as(self.user)
        self.page.goto(
            f"{self.live_server_url}/chat/{self.conv.uuid}?thread={self.root.uuid}"
        )
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        thread = self.page.locator("#thread-messages-container")
        first = thread.locator('[data-attachment-name="one.png"]')
        expect(first).to_be_visible()

        first.click()

        expect(self.page.get_by_title("Next attachment")).to_be_visible()

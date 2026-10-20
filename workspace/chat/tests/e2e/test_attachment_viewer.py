"""E2E: a chat attachment opens in the Files viewer modal.

Opening adds a history entry, so back closes the modal and forward brings
the attachment back. An attachment is not a Files file: the modal asks the
Files action registry nothing and shows no star. Its header carries chat's
own button, Save to Files.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, MessageAttachment
from workspace.common.tests.e2e.base import PlaywrightTestCase

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class ChatAttachmentViewerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="viewer-user")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        client = Client()
        client.force_login(self.user)
        resp = client.post(
            f"/api/v1/chat/conversations/{self.conv.uuid}/messages",
            {
                "body": "a picture",
                "files": [
                    SimpleUploadedFile("photo.png", PNG_1PX, content_type="image/png")
                ],
            },
        )
        assert resp.status_code == 201, resp.content
        self.attachment = MessageAttachment.objects.get(original_name="photo.png")

        self.login_as(self.user)
        self.conv_url = f"{self.live_server_url}/chat/{self.conv.uuid}"
        self.registry_requests = []
        self.page.on(
            "request",
            lambda r: (
                "/api/v1/files/actions" in r.url
                and self.registry_requests.append(r.url)
            ),
        )
        self.page.goto(self.conv_url)
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.viewer = self.page.locator("[data-file-viewer-modal] dialog")
        self.thumbnail = self.page.locator(
            '#messages-container [data-attachment-name="photo.png"]'
        )
        expect(self.thumbnail).to_be_visible()

    def test_back_closes_the_modal_and_forward_reopens_it(self):
        self.thumbnail.click()
        expect(self.viewer.get_by_role("heading", name="photo.png")).to_be_visible()
        expect(self.page).to_have_url(f"{self.conv_url}?open={self.attachment.uuid}")

        self.page.go_back()
        expect(self.viewer).to_have_js_property("open", False)
        expect(self.page).to_have_url(self.conv_url)

        self.page.go_forward()
        expect(self.viewer.get_by_role("heading", name="photo.png")).to_be_visible()
        expect(
            self.viewer.locator(f'#viewer-panel img[src*="{self.attachment.uuid}"]')
        ).to_be_visible()

        self.viewer.get_by_role("button", name="Close", exact=True).click()
        expect(self.viewer).to_have_js_property("open", False)
        expect(self.page).to_have_url(self.conv_url)

    def test_an_attachment_gets_no_star_and_asks_the_registry_nothing(self):
        self.thumbnail.click()
        expect(
            self.viewer.locator(f'#viewer-panel img[src*="{self.attachment.uuid}"]')
        ).to_be_visible()

        expect(self.viewer.locator("[data-viewer-favorite]")).to_be_hidden()
        self.assertEqual(self.registry_requests, [])

    def test_save_to_files_hands_the_attachment_to_chat(self):
        self.thumbnail.click()
        expect(self.viewer.get_by_role("heading", name="photo.png")).to_be_visible()

        self.viewer.get_by_role("button", name="Save to Files").click()

        expect(self.viewer).to_have_js_property("open", False)
        expect(self.page.get_by_text("Choose a destination folder.")).to_be_visible()

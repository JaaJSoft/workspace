"""E2E cover for the chat attachment viewer modal's stale-response race.

Open attachment A, then attachment B while A's viewer request is still in
flight: A's late response must neither paint its viewer into the modal nor
execute its scripts against B's DOM. A's request is held at the network
layer and released only after B has fully rendered - the worst-case ordering
the old hand-rolled loader lost (it appended A's markup below B's and ran
A's scripts).

The text viewer doubles as the script probe: its response carries a script
that defines ``window.textViewerMonaco`` at top level, so the global's very
existence proves A's scripts ran. B is an image - its viewer renders without
any third-party network.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64
import time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, MessageAttachment
from workspace.common.tests.e2e.base import PlaywrightTestCase

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class AttachmentViewerModalRaceTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="attachment-race")
        conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=conv, user=self.user)

        # Upload through the API so detection labels the attachments exactly
        # as production would.
        client = Client()
        client.force_login(self.user)
        resp = client.post(
            f"/api/v1/chat/conversations/{conv.uuid}/messages",
            {
                "body": "two attachments",
                "files": [
                    SimpleUploadedFile(
                        "alpha.txt",
                        b"alpha attachment body\n" * 40,
                        content_type="text/plain",
                    ),
                    SimpleUploadedFile("bravo.png", PNG_1PX, content_type="image/png"),
                ],
            },
        )
        assert resp.status_code == 201, resp.content
        self.text_att = MessageAttachment.objects.get(original_name="alpha.txt")
        self.image_att = MessageAttachment.objects.get(original_name="bravo.png")

    def _open_viewer(self, att):
        self.page.evaluate(
            """(a) => window.dispatchEvent(
                new CustomEvent('open-chat-attachment-viewer', { detail: a })
            )""",
            {
                "uuid": str(att.uuid),
                "name": att.original_name,
                "type": att.mime_type,
            },
        )

    def test_a_slow_previous_attachment_never_paints_into_the_new_ones_viewer(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        held = {}
        self.page.route(
            f"**/chat/view-attachment/{self.text_att.uuid}",
            lambda route: held.setdefault("text", route),
        )

        # Open A (text). Alpine may still be booting the modal's listeners,
        # so retry the dispatch until A's viewer request is actually held.
        deadline = time.monotonic() + 5
        while "text" not in held and time.monotonic() < deadline:
            self._open_viewer(self.text_att)
            self.page.wait_for_timeout(100)
        self.assertIn("text", held, "attachment A's viewer request must be in flight")

        # Open B (image) while A is still loading, and let it render fully.
        self._open_viewer(self.image_att)
        modal = self.page.locator('[x-data="chatAttachmentViewer()"] .modal-box')
        expect(modal.locator(f'img[src*="{self.image_att.uuid}"]')).to_be_visible(
            timeout=10000
        )

        # Worst-case ordering: A's response lands only now.
        with self.page.expect_response(f"**/chat/view-attachment/{self.text_att.uuid}"):
            held["text"].continue_()
        # Give a (buggy) merge every chance to paint before asserting.
        self.page.wait_for_timeout(300)

        # A's scripts must not have executed...
        self.assertEqual(
            self.page.evaluate("typeof window.textViewerMonaco"), "undefined"
        )
        # ...and A's viewer markup must not be in the modal - only B's.
        expect(modal.locator('[x-data^="textViewerMonaco"]')).to_have_count(0)
        expect(modal.locator(f'img[src*="{self.image_att.uuid}"]')).to_be_visible()

    def test_a_response_landing_after_close_never_mounts_into_the_closed_modal(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        held = {}
        self.page.route(
            f"**/chat/view-attachment/{self.text_att.uuid}",
            lambda route: held.setdefault("text", route),
        )

        deadline = time.monotonic() + 5
        while "text" not in held and time.monotonic() < deadline:
            self._open_viewer(self.text_att)
            self.page.wait_for_timeout(100)
        self.assertIn("text", held, "the viewer request must be in flight")

        # Close the modal while the viewer request is still hanging.
        dialog = self.page.locator('[x-data="chatAttachmentViewer()"] dialog')
        self.page.locator(
            '[x-data="chatAttachmentViewer()"] button[title="Close (ESC)"]'
        ).click()
        expect(dialog).to_have_js_property("open", False)

        # The response lands only now, into a closed modal.
        with self.page.expect_response(f"**/chat/view-attachment/{self.text_att.uuid}"):
            held["text"].continue_()
        # Give a (buggy) merge every chance to mount before asserting.
        self.page.wait_for_timeout(300)

        self.assertEqual(
            self.page.evaluate("typeof window.textViewerMonaco"), "undefined"
        )
        self.assertEqual(
            self.page.locator("#viewer-panel").evaluate("el => el.childElementCount"),
            0,
        )

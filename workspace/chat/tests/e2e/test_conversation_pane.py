"""E2E safety-net for the conversation pane partial extraction.

Asserts that after opening a conversation:
- the composer textarea is visible,
- #messages-container is in the DOM, and
- a message typed and sent by the user appears in #messages-container.

This test MUST pass both before and after the markup is moved from
index.html into partials/conversation_pane.html. If it goes red after the
move, the extraction broke something.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase


class ConversationPaneTests(PlaywrightTestCase):
    """Safety-net: conversation pane renders and accepts a message."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="pane-tester", password="pass12345")
        self.peer = self.create_user(username="pane-peer")

        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

    def test_pane_has_composer_and_messages_container_and_accepts_message(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        # Open the DM conversation by clicking the peer's name in the sidebar.
        # The sidebar is expanded by default on desktop viewports.
        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("pane-peer")).to_be_visible()
        list_root.get_by_text("pane-peer").click()

        # The conversation pane (inside <template x-if="activeConversation">)
        # renders after Alpine processes the selectConversationById() call.
        # The desktop composer textarea must become visible.
        composer = self.page.locator('textarea[placeholder="Type a message..."]')
        expect(composer).to_be_visible()

        # #messages-container is rendered inside the same x-if template.
        messages_container = self.page.locator("#messages-container")
        expect(messages_container).to_be_attached()

        # Type a message and send with Enter. The chat app injects an optimistic
        # bubble into #messages-container immediately (before SSE or re-fetch),
        # then replaces it with the server-rendered version once the POST returns.
        test_message = "hello from the safety-net test"
        composer.fill(test_message)
        composer.press("Enter")

        expect(messages_container.get_by_text(test_message)).to_be_visible()

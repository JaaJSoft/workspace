"""E2E cover for the right-click menu on chat messages.

The menu is bound once on each surface's messages container and reads its
state off the bubble's data attributes, so only a rendered page shows that
the attributes reach the bubble through the <chat-message-group> shell, that
the menu offers the right rows for own and peer messages, and that the thread
panel's menu and the main flow's never stay open together.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    PinnedMessage,
)
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase

MAIN_MENU = "#message-context-menu"
THREAD_MENU = "#thread-message-context-menu"


class MessageContextMenuTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="menu-tester")
        self.peer = self.create_user(username="menu-peer")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

        self.peer_msg = self._message(
            self.peer, "hello from the peer, see https://example.com"
        )
        self.own_msg = self._message(self.user, "my own message")

    def _message(self, author, body, **kwargs):
        return Message.objects.create(
            conversation=self.conv,
            author=author,
            body=body,
            body_html=render_message_body(body),
            **kwargs,
        )

    def _open_conversation(self):
        self.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=self.live_server_url
        )
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat/{self.conv.uuid}")
        expect(self._bubble(self.own_msg)).to_be_visible()
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _bubble(self, msg, prefix="msg"):
        return self.page.locator(f"#{prefix}-{msg.uuid} .msg-body")

    def _menu_row(self, label, menu=MAIN_MENU):
        return self.page.locator(menu).get_by_role("button", name=label, exact=True)

    def test_own_message_offers_edit_and_delete(self):
        self._open_conversation()
        self._bubble(self.own_msg).click(button="right")

        expect(self.page.locator(MAIN_MENU)).to_be_visible()
        for label in (
            "Reply",
            "Add reaction",
            "Copy text",
            "Pin message",
            "Edit",
            "Delete",
        ):
            expect(self._menu_row(label)).to_be_visible()
        expect(self._menu_row("Unpin message")).to_be_hidden()

    def test_peer_message_hides_edit_and_delete(self):
        self._open_conversation()
        # Away from the link: right-clicking the link keeps the browser's menu.
        self._bubble(self.peer_msg).click(button="right", position={"x": 5, "y": 5})

        expect(self._menu_row("Reply")).to_be_visible()
        expect(self._menu_row("Edit")).to_be_hidden()
        expect(self._menu_row("Delete")).to_be_hidden()

    def test_pinned_message_offers_unpin(self):
        PinnedMessage.objects.create(
            conversation=self.conv, message=self.own_msg, pinned_by=self.user
        )
        self._open_conversation()
        self._bubble(self.own_msg).click(button="right")

        expect(self._menu_row("Unpin message")).to_be_visible()
        expect(self._menu_row("Pin message")).to_be_hidden()

    def test_reply_quotes_the_message_in_the_composer(self):
        self._open_conversation()
        self._bubble(self.peer_msg).click(button="right", position={"x": 5, "y": 5})
        self._menu_row("Reply").click()

        expect(self.page.locator(MAIN_MENU)).to_be_hidden()
        expect(self.page.get_by_text("Replying to menu-peer")).to_be_visible()

    def test_copy_text_puts_the_raw_body_on_the_clipboard(self):
        self._open_conversation()
        self._bubble(self.own_msg).click(button="right")
        self._menu_row("Copy text").click()

        self.page.wait_for_function(
            "async () => (await navigator.clipboard.readText()) === 'my own message'"
        )

    def test_a_link_keeps_the_browser_menu(self):
        self._open_conversation()
        prevented = self.page.evaluate(
            """(uuid) => {
                const link = document.querySelector(`#msg-${uuid} .msg-body a`);
                const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
                link.dispatchEvent(event);
                return event.defaultPrevented;
            }""",
            str(self.peer_msg.uuid),
        )

        self.assertFalse(prevented)
        expect(self.page.locator(MAIN_MENU)).to_be_hidden()

    def test_opening_the_thread_menu_closes_the_main_one(self):
        self._message(
            self.peer, "a reply", reply_to=self.own_msg, thread_root=self.own_msg
        )
        Message.objects.filter(pk=self.own_msg.pk).update(reply_count=1)
        self._open_conversation()
        self.page.locator('[data-testid="thread-footer"]').click()
        thread_root = self._bubble(self.own_msg, prefix="tmsg")
        expect(thread_root).to_be_visible()

        self._bubble(self.own_msg).click(button="right")
        expect(self.page.locator(MAIN_MENU)).to_be_visible()
        thread_root.click(button="right")

        expect(self.page.locator(THREAD_MENU)).to_be_visible()
        expect(self.page.locator(MAIN_MENU)).to_be_hidden()

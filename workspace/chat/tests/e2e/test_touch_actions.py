"""Chat's hover-revealed actions on a touch screen.

A phone never hovers, so every control the desktop reveals on hover needs
its own way in there: the message toolbar follows a tap on the message
(focus), the conversation menu button and the info panel's row actions stay
on screen. Run in a context where ``(hover: none)`` matches.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    PinnedConversation,
)
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import TOUCH_CONTEXT, PlaywrightTestCase


class ChatTouchActionsTests(PlaywrightTestCase):
    CONTEXT_OPTIONS = TOUCH_CONTEXT

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="touch-tester")
        self.peer = self.create_user(username="touch-peer")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)
        self.peer_msg = self._message(self.peer, "a message from the peer")
        self.own_msg = self._message(self.user, "my own message")
        self.login_as(self.user)

    def _message(self, author, body):
        return Message.objects.create(
            conversation=self.conv,
            author=author,
            body=body,
            body_html=render_message_body(body),
        )

    def _open_conversation(self):
        self.page.goto(f"{self.live_server_url}/chat/{self.conv.uuid}")
        expect(self._bubble(self.own_msg)).to_be_visible()
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        # Opening a conversation ends by focusing the composer, once its
        # requests are back: a tap before that would lose the focus to it.
        self.page.wait_for_function(
            "document.activeElement && document.activeElement.tagName === 'TEXTAREA'"
        )

    def _bubble(self, msg):
        return self.page.locator(f"#msg-{msg.uuid} .msg-body")

    def _toolbar_button(self, msg, title):
        # The bubble and its toolbar share the shell's wrapper.
        return (
            self.page.locator(f"#msg-{msg.uuid}")
            .locator("xpath=..")
            .locator(f'button[title="{title}"]')
        )

    def test_the_context_has_no_hover(self):
        self.page.goto(f"{self.live_server_url}/login")
        self.assertTrue(self.page.evaluate("matchMedia('(hover: none)').matches"))

    def test_a_tap_on_a_message_reveals_its_toolbar(self):
        self._open_conversation()
        reply = self._toolbar_button(self.peer_msg, "Reply")
        toolbar = reply.locator("xpath=../..")
        expect(toolbar).to_have_css("opacity", "0")

        self._bubble(self.peer_msg).tap()

        expect(toolbar).to_have_css("opacity", "1")
        expect(toolbar).to_have_css("pointer-events", "auto")
        # The other message keeps its toolbar away.
        other = self._toolbar_button(self.own_msg, "Reply").locator("xpath=../..")
        expect(other).to_have_css("opacity", "0")

        reply.tap()
        expect(self.page.get_by_text("Replying to touch-peer")).to_be_visible()

    def test_a_tap_elsewhere_hides_the_toolbar_again(self):
        self._open_conversation()
        toolbar = self._toolbar_button(self.peer_msg, "Reply").locator("xpath=../..")
        self._bubble(self.peer_msg).tap()
        expect(toolbar).to_have_css("opacity", "1")

        self._bubble(self.own_msg).tap()

        expect(toolbar).to_have_css("opacity", "0")
        expect(toolbar).to_have_css("pointer-events", "none")

    def test_the_conversation_menu_button_stays_beside_the_time(self):
        # A tablet: a phone shows the conversation list as a rail of avatars.
        self.page.set_viewport_size({"width": 1024, "height": 768})
        self.page.goto(f"{self.live_server_url}/chat")
        item = self.page.locator(f"#conv-item-{self.conv.uuid}")
        expect(item).to_be_attached()
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(item.locator('[title="More options"]')).to_be_visible()
        expect(item.get_by_text("just now")).to_be_visible()

    def test_the_conversation_header_opens_the_conversation_menu(self):
        # The sidebar row's menu is out of reach on a phone: the rail clips
        # its button and iOS fires no contextmenu on a long press.
        self._open_conversation()
        self.page.get_by_test_id("conversation-header-menu").tap()

        menu = self.page.locator("#conv-context-menu > div")
        expect(menu).to_be_visible()
        box = menu.bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 412)

        with self.page.expect_response(
            lambda r: r.url.endswith(f"/conversations/{self.conv.uuid}/pin")
        ) as response:
            menu.get_by_role("button", name="Pin conversation", exact=True).tap()

        self.assertEqual(response.value.status, 201)
        expect(menu).to_be_hidden()
        self.assertTrue(
            PinnedConversation.objects.filter(
                owner=self.user, conversation=self.conv
            ).exists()
        )

    def test_staged_attachments_show_their_remove_button(self):
        self._open_conversation()
        file_input = self.page.locator('input[type="file"][x-ref="fileInput"]').first
        file_input.set_input_files(
            {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"}
        )
        # A file picked from Files stages as a second kind of chip.
        file_input.evaluate(
            "(el) => Alpine.$data(el).pendingPickedFiles.push("
            "{ uuid: 'picked', name: 'report.pdf' })"
        )
        chips = self.page.locator(r".group\/file")
        expect(chips).to_have_count(2)

        for name in ("notes.txt", "report.pdf"):
            chip = chips.filter(has_text=name)
            remove = chip.locator("button")
            expect(remove).to_have_css("opacity", "1")
            remove.tap()
            expect(chip).to_have_count(0)

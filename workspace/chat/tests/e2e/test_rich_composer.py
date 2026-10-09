"""E2E: the formatted composer shows a message as it will be sent and sends
the markdown the server renders.

The editor is a vendored ProseMirror bundle loaded as an ES module, so only a
real browser exercises it: the markdown it serializes, the keys it shares with
the page (Enter sends, Shift+Enter breaks the line), and the switch to the raw
markdown mode.
"""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.services.settings import get_setting, set_setting


class RichComposerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="writer")
        self.peer = self.create_user(username="jane.doe", first_name="Jane")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Team", created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open_conversation(self):
        self.page.goto(f"{self.live_server_url}/chat/{self.conv.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        editor = self._editor()
        expect(editor).to_be_visible()
        # Opening a conversation ends by focusing the composer; typing before
        # that would lose the first keystrokes to the focus move.
        expect(editor).to_be_focused()
        return editor

    def _editor(self):
        return self.page.locator(".chat-rich-input:visible")

    def _markdown_textarea(self):
        return self.page.locator('textarea[x-ref="messageInput"]')

    def _press_enter_and_wait(self, method):
        with self.page.expect_response(
            lambda r: "/messages" in r.url and r.request.method == method
        ):
            self.page.keyboard.press("Enter")

    def _send_and_get_body(self):
        self._press_enter_and_wait("POST")
        return Message.objects.filter(conversation=self.conv).latest("created_at").body

    def test_shortcuts_apply_the_style_instead_of_inserting_markers(self):
        editor = self._open_conversation()
        self.page.keyboard.type("Hello ")
        self.page.keyboard.press("Control+b")
        self.page.keyboard.type("bold")
        self.page.keyboard.press("Control+b")
        self.page.keyboard.type(" and ")
        self.page.keyboard.press("Control+i")
        self.page.keyboard.type("italic")
        self.page.keyboard.press("Control+i")
        self.page.keyboard.type(" with ")
        # Code stays on for everything typed until toggled off, like bold.
        self.page.keyboard.press("Control+e")
        self.page.keyboard.type("SOME_FLAG")
        self.page.keyboard.press("Control+e")
        self.page.keyboard.type(" set")

        expect(editor.locator("strong")).to_have_text("bold")
        expect(editor.locator("em")).to_have_text("italic")
        expect(editor.locator("code")).to_have_text("SOME_FLAG")
        expect(editor).not_to_contain_text("**")
        self.assertEqual(
            self._send_and_get_body(),
            "Hello **bold** and *italic* with `SOME_FLAG` set",
        )
        expect(editor).to_have_text("")

    def test_plain_prose_is_sent_without_backslash_escapes(self):
        self._open_conversation()
        text = "snake_case and 5 * 3 at 10:30 [WIP] see https://example.com/a_b?x=1&y=2"
        self.page.keyboard.type(text)

        self.assertEqual(self._send_and_get_body(), text)

    def test_shift_enter_breaks_the_line_and_a_marker_starts_a_list(self):
        editor = self._open_conversation()
        self.page.keyboard.type("line one")
        self.page.keyboard.press("Shift+Enter")
        self.page.keyboard.type("line two")
        self.page.keyboard.press("Shift+Enter")
        self.page.keyboard.type("- first")
        self.page.keyboard.press("Shift+Enter")
        self.page.keyboard.type("second")

        expect(editor.locator("li")).to_have_count(2)
        self.assertEqual(
            self._send_and_get_body(), "line one\nline two\n\n- first\n- second"
        )

    def test_mention_autocomplete_inserts_the_username(self):
        self._open_conversation()
        self.page.keyboard.type("ping @jan")
        option = self.page.get_by_role("button", name="@jane.doe")
        expect(option).to_be_visible()
        self.page.keyboard.press("Enter")
        self.page.keyboard.type("please")

        self.assertEqual(self._send_and_get_body(), "ping @jane.doe please")

    def test_arrow_up_loads_the_last_message_formatted_for_editing(self):
        Message.objects.create(
            conversation=self.conv, author=self.user, body="**hi** there"
        )
        editor = self._open_conversation()
        self.page.keyboard.press("ArrowUp")

        expect(editor.locator("strong")).to_have_text("hi")
        self.page.keyboard.type(" again")
        self._press_enter_and_wait("PATCH")
        expect(editor).to_have_text("")
        self.assertEqual(
            Message.objects.get(conversation=self.conv).body, "**hi** there again"
        )

    def test_markdown_mode_shows_the_raw_text_and_is_remembered(self):
        editor = self._open_conversation()
        self.page.keyboard.press("Control+b")
        self.page.keyboard.type("loud")

        with self.page.expect_response(
            lambda r: r.url.endswith("/api/v1/settings/chat/preferences")
        ):
            self.page.get_by_title("Edit as markdown").click()
        textarea = self._markdown_textarea()
        expect(textarea).to_be_visible()
        expect(textarea).to_have_value("**loud**")
        expect(editor).to_have_count(0)
        self.assertEqual(
            get_setting(self.user, "chat", "preferences")["composerMode"], "markdown"
        )

        # The markdown mode inserts the syntax, as it always has.
        textarea.press("End")
        textarea.press("Control+i")
        expect(textarea).to_have_value("**loud****")

        self.page.reload()
        expect(self._markdown_textarea()).to_be_visible()
        expect(self.page.locator(".chat-rich-input")).to_be_hidden()

    def test_switching_back_shows_the_markdown_typed_meanwhile_formatted(self):
        set_setting(self.user, "chat", "preferences", {"composerMode": "markdown"})
        self.page.goto(f"{self.live_server_url}/chat/{self.conv.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        textarea = self._markdown_textarea()
        expect(textarea).to_be_focused()
        textarea.type("a `snippet`")

        self.page.get_by_title("Back to formatted text").click()
        expect(self._editor().locator("code")).to_have_text("snippet")
        self.assertEqual(self._send_and_get_body(), "a `snippet`")

"""E2E: the open conversation is browser history.

Opening a conversation from the list adds an entry and switching to another
one rewrites it, so back returns to the list. Leaving a conversation for the
list (the mobile back arrow, leaving it for good) steps back over the entry
that opened it rather than pushing a new /chat on top: a browser back
afterwards must never reopen what was just closed.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase

MOBILE_VIEWPORT = {"width": 390, "height": 844}


class ChatHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="history-tester")
        self.conv_a = self._dm(self.create_user(username="peer-alpha"))
        self.conv_b = self._dm(self.create_user(username="peer-bravo"))
        self.list_url = f"{self.live_server_url}/chat"
        self.login_as(self.user)

    def _dm(self, peer):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=conv, user=self.user)
        ConversationMember.objects.create(conversation=conv, user=peer)
        return conv

    def _open(self, url):
        self.page.goto(url)
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _row(self, conv):
        return self.page.locator(f"#conv-item-{conv.uuid} > button")

    def _header(self, name):
        return self.page.get_by_role("heading", name=name, exact=True)

    def _expect_list(self):
        expect(self.page).to_have_url(self.list_url)
        expect(self.page.get_by_role("heading", name="Welcome to Chat")).to_be_visible()

    def test_back_after_switching_conversations_returns_to_the_list(self):
        self._open(self.list_url)

        self._row(self.conv_a).click()
        expect(self._header("peer-alpha")).to_be_visible()
        self._row(self.conv_b).click()
        expect(self._header("peer-bravo")).to_be_visible()
        expect(self.page).to_have_url(f"{self.list_url}/{self.conv_b.uuid}")

        self.page.go_back()
        self._expect_list()

        self.page.go_forward()
        expect(self.page).to_have_url(f"{self.list_url}/{self.conv_b.uuid}")
        expect(self._header("peer-bravo")).to_be_visible()

    def test_mobile_back_arrow_then_browser_back_does_not_reopen(self):
        self.page.set_viewport_size(MOBILE_VIEWPORT)
        self._open(self.list_url)

        self._row(self.conv_a).click()
        expect(self._header("peer-alpha")).to_be_visible()
        self.page.get_by_role("button", name="Back to conversations").click()
        self._expect_list()

        # The arrow undid the opening: forward reopens the conversation.
        self.page.go_forward()
        expect(self.page).to_have_url(f"{self.list_url}/{self.conv_a.uuid}")
        expect(self._header("peer-alpha")).to_be_visible()

        self.page.get_by_role("button", name="Back to conversations").click()
        self._expect_list()

        # /chat was the first page of the tab: back leaves the app.
        self.page.go_back()
        expect(self.page).to_have_url("about:blank")

    def test_mobile_back_arrow_on_a_deep_link_lands_on_the_list_in_place(self):
        self.page.set_viewport_size(MOBILE_VIEWPORT)
        self._open(f"{self.list_url}/{self.conv_a.uuid}")
        expect(self._header("peer-alpha")).to_be_visible()
        entries = self.page.evaluate("history.length")

        self.page.get_by_role("button", name="Back to conversations").click()

        self._expect_list()
        self.assertEqual(self.page.evaluate("history.length"), entries)

    def test_leaving_a_conversation_does_not_leave_it_behind_in_history(self):
        self._open(self.list_url)

        self._row(self.conv_a).click(button="right")
        self.page.locator("#conv-context-menu").get_by_text(
            "Leave conversation"
        ).click()
        self.page.locator("#app-dialog-confirm-ok").click()
        self._expect_list()
        expect(self._row(self.conv_a)).to_have_count(0)

        self.page.go_back()
        expect(self.page).to_have_url("about:blank")

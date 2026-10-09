"""E2E: arrowing through a long result list keeps the highlighted entry in
view, in the "New conversation" user search and in the message search panel.

Both lists scroll inside a capped box, so an entry below the fold only shows
up if the keyboard handler scrolls it in.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.common.tests.e2e.base import PlaywrightTestCase

IN_VIEW = """
(el) => {
  const box = el.closest('.overflow-y-auto').getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
}
"""


class SearchKeyboardScrollTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")

    def _goto_chat(self, path="/chat"):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _press_down(self, times):
        for _ in range(times):
            self.page.keyboard.press("ArrowDown")

    def test_user_search_scrolls_the_highlighted_user_into_view(self):
        # The dialog asks for ten users at most.
        for i in range(10):
            self.create_user(username=f"bob{i:02d}")
        self._goto_chat()
        self.page.get_by_title("New conversation (Alt+N)").click()

        with self.page.expect_response(lambda r: "/api/v1/users/search" in r.url):
            self.page.locator('input[x-ref="userSearchInput"]').fill("bob")
        options = self.page.locator('[x-ref="userSearchList"] button')
        expect(options).to_have_count(10)

        self._press_down(10)
        active = self.page.locator('[data-search-active="true"]')
        expect(active).to_have_count(1)
        self.page.wait_for_function(IN_VIEW, arg=active.element_handle())

    def test_message_search_scrolls_the_highlighted_result_into_view(self):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Notes", created_by=self.user
        )
        ConversationMember.objects.create(conversation=conv, user=self.user)
        for i in range(25):
            Message.objects.create(
                conversation=conv, author=self.user, body=f"needle number {i}"
            )
        self._goto_chat(f"/chat/{conv.uuid}")
        # Opening the conversation ends by focusing the composer.
        expect(self.page.locator(".chat-rich-input")).to_be_focused()

        self.page.keyboard.press("Control+f")
        # The panel exists twice, one copy per breakpoint.
        search = self.page.locator("input[data-search-input]:visible")
        expect(search).to_be_focused()
        with self.page.expect_response(lambda r: "/messages/search" in r.url) as resp:
            search.fill("needle")
        count = len(resp.value.json()["results"])
        self.assertGreaterEqual(count, 20)
        # The response lands before the component has stored the results.
        self.page.wait_for_function(
            "(n) => Alpine.$data(document.querySelector('[data-search-input]'))"
            ".searchResults.length === n",
            arg=count,
        )

        self._press_down(count)
        # Alpine drops a data attribute bound to false: only the highlight has it.
        active = self.page.locator('[data-search-result-active="true"]:visible')
        expect(active).to_have_count(1)
        self.page.wait_for_function(IN_VIEW, arg=active.element_handle())

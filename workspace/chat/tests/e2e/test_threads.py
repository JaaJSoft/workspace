"""E2E cover for threaded replies.

Three things only a rendered page can prove: a threaded reply really is absent
from the main flow, the panel really holds the whole thread, and writing in the
panel really lands in the thread rather than in the conversation.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import time

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase


class ThreadPanelTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="thread-tester", password="pass12345")
        self.peer = self.create_user(username="thread-peer")

        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

        self.root = self._message("the root of the discussion")
        self._message(
            "a reply hidden in the thread",
            reply_to=self.root,
            thread_root=self.root,
        )
        Message.objects.filter(pk=self.root.pk).update(reply_count=1)

    def _message(self, body, **kwargs):
        # body_html, not just body: the bubble renders the rendered HTML and
        # skips the block entirely when it is empty, so a message built with
        # body alone is invisible on the page.
        return Message.objects.create(
            conversation=self.conv,
            author=self.peer,
            body=body,
            body_html=render_message_body(body),
            **kwargs,
        )

    def _open_conversation(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        # The row, not a text match: this conversation has messages, so the
        # peer's name appears twice in the sidebar (title and last-message
        # preview) and a bare get_by_text is a strict-mode violation.
        row = self.page.locator("#conversation-list button").first
        expect(row).to_be_visible()
        row.click()
        expect(self.page.locator("#messages-container")).to_be_attached()
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _open_thread(self):
        footer = self.page.locator('[data-testid="thread-footer"]')
        expect(footer).to_contain_text("1 reply")
        footer.click()
        expect(self.page.locator("#thread-messages-container")).to_be_attached()

    def test_a_threaded_reply_is_absent_from_the_main_flow(self):
        self._open_conversation()
        main_flow = self.page.locator("#messages-container")
        expect(main_flow.get_by_text("the root of the discussion")).to_be_visible()
        expect(main_flow.get_by_text("a reply hidden in the thread")).to_have_count(0)

    def test_the_footer_opens_a_panel_holding_the_whole_thread(self):
        self._open_conversation()
        self._open_thread()

        panel = self.page.locator("#thread-messages-container")
        expect(panel.get_by_text("the root of the discussion")).to_be_visible()
        expect(panel.get_by_text("a reply hidden in the thread")).to_be_visible()

    def test_writing_in_the_panel_lands_in_the_thread_not_the_conversation(self):
        self._open_conversation()
        self._open_thread()

        composer = self.page.locator(
            '[data-testid="thread-composer"] textarea[placeholder="Type a message..."]'
        )
        sent = "written from inside the panel"
        composer.fill(sent)
        composer.press("Enter")

        expect(
            self.page.locator("#thread-messages-container").get_by_text(sent)
        ).to_be_visible()
        expect(
            self.page.locator("#messages-container").get_by_text(sent)
        ).to_have_count(0)

        # The server agrees: the reply is anchored to the thread, not loose in
        # the conversation. Polled, because the visible bubble above can be the
        # optimistic copy injected before the POST commits - asserting the row
        # immediately races the server.
        deadline = time.monotonic() + 10
        while (
            not Message.objects.filter(body=sent).exists()
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        posted = Message.objects.get(body=sent)
        self.assertEqual(posted.thread_root_id, self.root.uuid)

    def test_a_notification_deep_link_opens_the_thread_panel(self):
        # A thread-reply notification points at /chat/<conv>?thread=<root>: the
        # reply is not in the main flow, so without the panel opening on
        # arrival the page would show nothing new.
        self.login_as(self.user)
        self.page.goto(
            f"{self.live_server_url}/chat/{self.conv.uuid}?thread={self.root.uuid}"
        )
        panel = self.page.locator("#thread-messages-container")
        expect(panel).to_be_attached()
        expect(panel.get_by_text("a reply hidden in the thread")).to_be_visible()

    def test_opening_a_second_thread_switches_the_panel_to_it(self):
        # x-if only remounts through a falsy value, so switching roots relies
        # on openThread bouncing through null - a panel stuck on the first
        # thread is exactly what that would look like.
        other_root = self._message("another discussion entirely")
        self._message(
            "the reply in the second thread",
            reply_to=other_root,
            thread_root=other_root,
        )
        Message.objects.filter(pk=other_root.pk).update(reply_count=1)

        self._open_conversation()
        footers = self.page.locator('[data-testid="thread-footer"]')
        expect(footers).to_have_count(2)
        footers.first.click()
        panel = self.page.locator("#thread-messages-container")
        expect(panel.get_by_text("a reply hidden in the thread")).to_be_visible()

        footers.nth(1).click()
        expect(panel.get_by_text("the reply in the second thread")).to_be_visible()
        expect(panel.get_by_text("a reply hidden in the thread")).to_have_count(0)

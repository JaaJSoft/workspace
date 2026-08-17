"""E2E cover for rapid conversation switching.

The conversation pane is NOT torn down on a switch, so a slow response for
the previous conversation could in principle paint over the one the user
just opened. Instead of racing real timing, both conversations' message
requests are intercepted and held, then released in the worst possible
order: the OLD conversation's response arrives first, while the new one is
still loading. Only a rendered page can prove the late response is dropped
(alpine-ajax's newest-request-per-target bookkeeping plus the
data-conversation-uuid merge veto) rather than displayed.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import time

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase


class RapidConversationSwitchTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="switch-tester", password="pass12345")
        self.peer_a = self.create_user(username="peer-alpha")
        self.peer_b = self.create_user(username="peer-bravo")
        self.conv_a = self._conversation(self.peer_a, "message from alpha conversation")
        self.conv_b = self._conversation(self.peer_b, "message from bravo conversation")

    def _conversation(self, peer, body):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=conv, user=self.user)
        ConversationMember.objects.create(conversation=conv, user=peer)
        Message.objects.create(
            conversation=conv,
            author=peer,
            body=body,
            body_html=render_message_body(body),
        )
        return conv

    def _hold_messages(self, conv, held):
        # The handler keeps the route un-answered: the request stays in
        # flight until the test explicitly releases it with continue_().
        self.page.route(
            f"**/chat/{conv.uuid}/messages*",
            lambda route: held.setdefault(str(conv.uuid), route),
        )

    def test_a_slow_previous_conversation_never_paints_over_the_new_one(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        held = {}
        self._hold_messages(self.conv_a, held)
        self._hold_messages(self.conv_b, held)

        rows = self.page.locator("#conversation-list button")
        row_a = rows.filter(has_text="peer-alpha").first
        row_b = rows.filter(has_text="peer-bravo").first
        expect(row_a).to_be_visible()
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        # Open A, then switch to B while A's request is still hanging.
        row_a.click()
        row_b.click()

        deadline = time.monotonic() + 5
        while len(held) < 2 and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertEqual(
            len(held), 2, "both conversations' message requests must be in flight"
        )

        flow = self.page.locator("#messages-container")

        # Worst-case ordering: the OLD conversation's response lands first,
        # while the pane is empty and waiting for B - nothing else has
        # painted, so only the staleness handling keeps A off the screen.
        with self.page.expect_response(f"**/chat/{self.conv_a.uuid}/messages*"):
            held[str(self.conv_a.uuid)].continue_()
        # Give a (buggy) merge every chance to paint before asserting.
        self.page.wait_for_timeout(200)
        expect(flow.get_by_text("message from alpha conversation")).to_have_count(0)

        # The conversation the user actually chose lands normally.
        with self.page.expect_response(f"**/chat/{self.conv_b.uuid}/messages*"):
            held[str(self.conv_b.uuid)].continue_()
        expect(flow.get_by_text("message from bravo conversation")).to_be_visible()
        expect(flow.get_by_text("message from alpha conversation")).to_have_count(0)

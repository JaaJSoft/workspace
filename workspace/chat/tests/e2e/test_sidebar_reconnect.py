"""End-to-end test: a stream reconnection redraws the chat sidebar rows.

The rows are server-rendered, so reloading the conversation data alone
leaves them showing what they showed before the stream went down.

Skipped unless ``E2E=1`` is set.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase


class ChatSidebarReconnectTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="viewer", password="pass12345")
        self.peer_a = self.create_user(username="alice-contact")
        self.peer_b = self.create_user(username="bob-contact")
        self.peer_c = self.create_user(username="carol-contact")
        self.conv_a = self._conversation(self.peer_a)
        self.conv_b = self._conversation(self.peer_b)
        self._message(self.conv_a, self.peer_a, "alpha old line")
        self._message(self.conv_b, self.peer_b, "bravo old line")
        # A is the most recent, so it is listed first.
        now = timezone.now()
        Conversation.objects.filter(pk=self.conv_a.pk).update(updated_at=now)
        Conversation.objects.filter(pk=self.conv_b.pk).update(
            updated_at=now - timedelta(minutes=10)
        )

    def _conversation(self, peer):
        conv = Conversation.objects.create(kind=Conversation.Kind.DM, created_by=peer)
        ConversationMember.objects.create(conversation=conv, user=self.user)
        ConversationMember.objects.create(conversation=conv, user=peer)
        return conv

    def _message(self, conv, author, body):
        Message.objects.create(
            conversation=conv,
            author=author,
            body=body,
            body_html=render_message_body(body),
        )

    def _row_texts(self):
        return self.page.locator("#conversation-list li").all_inner_texts()

    def test_reconnect_redraws_rows_changed_while_the_stream_was_down(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("bravo old line")).to_be_visible()

        # What happens while the stream is down: a message in B, a new
        # conversation C. The page hears of neither.
        self._message(self.conv_b, self.peer_b, "bravo new line")
        Conversation.objects.filter(pk=self.conv_b.pk).update(
            updated_at=timezone.now() + timedelta(minutes=1)
        )
        conv_c = self._conversation(self.peer_c)
        self._message(conv_c, self.peer_c, "carol first line")

        self.page.evaluate("window.dispatchEvent(new CustomEvent('sse:reconnect'))")

        expect(list_root.get_by_text("bravo new line")).to_be_visible()
        expect(list_root.get_by_text("bravo old line")).to_have_count(0)
        expect(list_root.get_by_text("carol first line")).to_be_visible()
        rows = self._row_texts()
        first_b = next(i for i, t in enumerate(rows) if "bob-contact" in t)
        first_a = next(i for i, t in enumerate(rows) if "alice-contact" in t)
        self.assertLess(first_b, first_a, "B received the latest message")

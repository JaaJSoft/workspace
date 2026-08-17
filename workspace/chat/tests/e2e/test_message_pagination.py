"""E2E cover for the "load older messages" pagination.

Only a rendered page can prove the alpine-ajax prepend really works: that an
older page lands ABOVE the messages already on screen in chronological order,
without duplicating them, that the pagination state follows the response, and
that the viewport stays anchored on the message the user was reading.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase

# The messages endpoint pages by 50; 60 seeded messages leave exactly one
# older page (001-010) behind the first load (011-060).
PAGE_SIZE = 50
SEEDED = 60


class MessagePaginationTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="page-tester", password="pass12345")
        self.peer = self.create_user(username="page-peer")

        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

        # Explicit, strictly increasing timestamps: the cursor filters on
        # created_at__lt, so a tie between two auto_now_add stamps could
        # silently drop a message from either page.
        base = timezone.now() - timedelta(hours=1)
        for i in range(1, SEEDED + 1):
            msg = Message.objects.create(
                conversation=self.conv,
                author=self.peer,
                body=f"pagination message {i:03d}",
                body_html=render_message_body(f"pagination message {i:03d}"),
            )
            Message.objects.filter(pk=msg.pk).update(
                created_at=base + timedelta(seconds=i)
            )

    def _open_conversation(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        row = self.page.locator("#conversation-list button").first
        expect(row).to_be_visible()
        row.click()
        expect(self.page.locator("#messages-container")).to_be_attached()
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _bubble_texts(self):
        return self.page.eval_on_selector_all(
            "#message-list-items .msg-bubble[data-body]",
            "els => els.map(el => el.dataset.body)",
        )

    # The pane's scroll container ($refs.messagesContainer). Scrolling it to
    # the top is the trigger both tests use: it is the real user gesture, and
    # clicking the "Load older" button instead would race the very same
    # scroll-triggered load that Playwright's scroll-into-view sets off.
    SCROLLER = 'document.querySelector(\'div[x-ref="messagesContainer"]\')'

    def test_scrolling_to_the_top_prepends_the_previous_page_in_order(self):
        self._open_conversation()

        flow = self.page.locator("#messages-container")
        expect(flow.get_by_text(f"pagination message {SEEDED:03d}")).to_be_visible()
        expect(flow.get_by_text("pagination message 001")).to_have_count(0)
        button = self.page.get_by_role("button", name="Load older messages")
        expect(button).to_be_visible()

        self.page.evaluate(f"{self.SCROLLER}.scrollTop = 0")

        expect(flow.get_by_text("pagination message 001")).to_be_attached()
        texts = self._bubble_texts()
        self.assertEqual(len(texts), SEEDED, "no message may be dropped or duplicated")
        self.assertEqual(
            texts,
            [f"pagination message {i:03d}" for i in range(1, SEEDED + 1)],
            "the older page must sit above the existing messages, oldest first",
        )

        # Everything is loaded: the pagination state followed the response.
        expect(button).to_be_hidden()

    def test_loading_older_messages_keeps_the_viewport_anchored(self):
        self._open_conversation()
        expect(
            self.page.locator("#messages-container").get_by_text(
                f"pagination message {SEEDED:03d}"
            )
        ).to_be_visible()

        before_height = self.page.evaluate(f"{self.SCROLLER}.scrollHeight")
        self.page.evaluate(f"{self.SCROLLER}.scrollTop = 0")
        expect(
            self.page.locator("#messages-container").get_by_text(
                "pagination message 001"
            )
        ).to_be_attached()

        # The restore sets scrollTop to exactly the height the prepend added,
        # which puts the message the user was reading back at the top of the
        # viewport instead of teleporting them 50 messages up.
        self.page.wait_for_function(
            f"""() => {{
              const el = {self.SCROLLER};
              return Math.abs(el.scrollTop - (el.scrollHeight - {before_height})) < 40;
            }}"""
        )

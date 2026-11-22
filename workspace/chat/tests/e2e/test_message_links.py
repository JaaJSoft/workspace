"""E2E cover for links to one message.

Only a rendered page can prove the deep link lands on the message (and not on
the end of the conversation), that newer pages append below it in order when
the reader scrolls down, and that a thread reply opens its thread.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase

SEEDED = 200


class MessageLinkTests(PlaywrightTestCase):
    SCROLLER = "document.querySelector('div[x-ref=\"messagesContainer\"]')"

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="link-tester", password="pass12345")
        self.peer = self.create_user(username="link-peer")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

        base = timezone.now() - timedelta(hours=5)
        self.messages = []
        for i in range(1, SEEDED + 1):
            body = f"linked message {i:03d}"
            msg = Message.objects.create(
                conversation=self.conv,
                author=self.peer,
                body=body,
                body_html=render_message_body(body),
            )
            Message.objects.filter(pk=msg.pk).update(
                created_at=base + timedelta(seconds=i)
            )
            self.messages.append(msg)

    def _open(self, query):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat/{self.conv.uuid}?{query}")
        expect(self.page.locator("#messages-container")).to_be_attached()
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _bubble_texts(self):
        return self.page.eval_on_selector_all(
            "#message-list-items .msg-bubble[data-body]",
            "els => els.map(el => el.dataset.body)",
        )

    def test_a_link_opens_the_conversation_on_an_old_message(self):
        target = self.messages[19]  # 020, 180 messages behind the latest one
        self._open(f"message={target.uuid}")

        bubble = self.page.locator(f"#msg-{target.uuid}")
        expect(bubble).to_be_in_viewport()
        expect(
            self.page.locator("#messages-container").get_by_text(
                f"linked message {SEEDED:03d}"
            )
        ).to_have_count(0)
        expect(self.page.get_by_role("button", name="Latest messages")).to_be_visible()
        # The address no longer carries the link once it is followed.
        self.assertEqual(self.page.evaluate("location.search"), "")

    def test_scrolling_down_appends_the_newer_pages_in_order(self):
        self._open(f"message={self.messages[19].uuid}")
        expect(self.page.locator(f"#msg-{self.messages[19].uuid}")).to_be_in_viewport()

        # Re-assert the bottom every frame until the present is reached: each
        # merged page grows the list and moves the bottom away again.
        self.page.wait_for_function(
            f"""() => {{
              const newest = document.querySelector(
                '#message-list-items .msg-bubble[data-body="linked message {SEEDED:03d}"]');
              if (newest) return true;
              const el = {self.SCROLLER};
              if (el) el.scrollTop = el.scrollHeight;
              return false;
            }}""",
            timeout=15000,
        )

        texts = self._bubble_texts()
        first = int(texts[0].split()[-1])
        self.assertEqual(
            texts,
            [f"linked message {i:03d}" for i in range(first, SEEDED + 1)],
            "newer pages must land below, in order, with nothing dropped or repeated",
        )
        expect(self.page.get_by_role("button", name="Latest messages")).to_be_hidden()

    def test_latest_messages_jumps_back_to_the_present(self):
        self._open(f"message={self.messages[19].uuid}")
        button = self.page.get_by_role("button", name="Latest messages")
        expect(button).to_be_visible()

        button.click()

        expect(
            self.page.locator("#messages-container").get_by_text(
                f"linked message {SEEDED:03d}"
            )
        ).to_be_in_viewport()
        expect(button).to_be_hidden()

    def test_a_link_to_an_unknown_message_opens_the_latest_page(self):
        self._open("message=00000000-0000-4000-8000-000000000000")

        expect(
            self.page.locator("#messages-container").get_by_text(
                f"linked message {SEEDED:03d}"
            )
        ).to_be_visible()
        expect(self.page.get_by_text("This message is not available.")).to_be_visible()

    def test_a_link_to_a_thread_reply_opens_its_thread_on_it(self):
        root = Message.objects.get(pk=self.messages[49].pk)
        reply = Message.objects.create(
            conversation=self.conv,
            author=self.user,
            body="the reply behind the link",
            body_html=render_message_body("the reply behind the link"),
            thread_root=root,
        )
        Message.objects.filter(pk=reply.pk).update(
            created_at=root.created_at + timedelta(milliseconds=500)
        )

        self._open(f"message={reply.uuid}")

        expect(self.page.locator(f"#tmsg-{reply.uuid}")).to_be_visible()
        expect(self.page.locator(f"#msg-{root.uuid}")).to_be_attached()

    def test_copy_link_gives_the_address_of_the_message(self):
        self.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=self.live_server_url
        )
        target = self.messages[-1]
        self._open("")
        bubble = self.page.locator(f"#msg-{target.uuid}")
        expect(bubble).to_be_visible()

        bubble.click(button="right")
        self.page.locator("#message-context-menu").get_by_text("Copy link").click()

        expect(self.page.get_by_text("Link copied")).to_be_visible()
        copied = self.page.evaluate("navigator.clipboard.readText()")
        self.assertEqual(
            copied,
            f"{self.live_server_url}/chat/{self.conv.uuid}?message={target.uuid}",
        )

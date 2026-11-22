"""Links to one message: the around and after cursors of the message lists.

A link opens the conversation on a page cut around the message, however far
back it is, and the reader scrolls down to the present with after pages. A
message the reader cannot see - another conversation, another thread - is
answered exactly like one that does not exist.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.message_pages import PAGE_SIZE
from workspace.users.services.settings import set_setting

User = get_user_model()

BASE = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
COUNT = 120


def _bodies(response):
    return re.findall(
        r'data-body="numbered message (\d{3})"', response.content.decode()
    )


def _state(response, list_id="message-list"):
    """The attributes of the list's state element, as a dict."""
    html = response.content.decode()
    match = re.search(rf'<div\s+id="{list_id}-state"(.*?)>\s*</div>', html, re.S)
    assert match, "no state element"
    return dict(re.findall(r'(data-[\w-]+)="([^"]*)"', match.group(1)))


def _numbered(conversation, author, count, start=BASE, **fields):
    messages = []
    for i in range(1, count + 1):
        msg = Message.objects.create(
            conversation=conversation,
            author=author,
            body=f"numbered message {i:03d}",
            **fields,
        )
        # Strictly increasing, all on one day, and more than the five minutes
        # that would merge one author's messages into a group.
        Message.objects.filter(pk=msg.pk).update(
            created_at=start + timedelta(minutes=6 * i)
        )
        messages.append(msg)
    return messages


class MessageListCursorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice-links", password="x")
        cls.bob = User.objects.create_user(username="bob-links", password="x")
        cls.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Links", created_by=cls.alice
        )
        for user in (cls.alice, cls.bob):
            ConversationMember.objects.create(conversation=cls.conv, user=user)
        cls.messages = _numbered(cls.conv, cls.bob, COUNT)

        # A conversation alice is not in, with a message dated in the middle
        # of hers: used as a cursor it would cut her pages at its timestamp.
        cls.foreign = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Elsewhere", created_by=cls.bob
        )
        ConversationMember.objects.create(conversation=cls.foreign, user=cls.bob)
        cls.foreign_msg = Message.objects.create(
            conversation=cls.foreign, author=cls.bob, body="foreign"
        )
        Message.objects.filter(pk=cls.foreign_msg.pk).update(
            created_at=BASE + timedelta(minutes=305)
        )

    def setUp(self):
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def get(self, **params):
        return self.client.get(f"/chat/{self.conv.uuid}/messages", params)

    def test_around_opens_a_page_centred_on_the_message(self):
        target = self.messages[29]  # 030, far behind the latest page
        response = self.get(around=target.uuid)

        bodies = _bodies(response)
        half = PAGE_SIZE // 2
        self.assertEqual(bodies[0], f"{30 - half:03d}")
        self.assertEqual(bodies[-1], f"{30 + half - 1:03d}")
        self.assertEqual(len(bodies), PAGE_SIZE)
        state = _state(response)
        self.assertEqual(state["data-has-more"], "true")
        self.assertEqual(state["data-has-newer"], "true")
        self.assertEqual(state["data-target-uuid"], str(target.uuid))
        self.assertEqual(state["data-first-uuid"], str(self.messages[29 - half].uuid))
        self.assertEqual(
            state["data-last-uuid"], str(self.messages[29 + half - 1].uuid)
        )

    def test_around_the_first_message_has_nothing_older(self):
        response = self.get(around=self.messages[0].uuid)
        self.assertEqual(_bodies(response)[0], "001")
        self.assertEqual(_state(response)["data-has-more"], "false")

    def test_around_a_recent_message_reaches_the_present(self):
        response = self.get(around=self.messages[-5].uuid)
        self.assertEqual(_bodies(response)[-1], f"{COUNT:03d}")
        self.assertEqual(_state(response)["data-has-newer"], "false")

    def test_after_pages_forward_into_the_tail(self):
        cursor = self.messages[49]  # 050
        response = self.get(after=cursor.uuid)

        self.assertEqual(_bodies(response), [f"{i:03d}" for i in range(51, 101)])
        state = _state(response)
        self.assertEqual(state["data-has-newer"], "true")
        self.assertEqual(state["data-last-uuid"], str(self.messages[99].uuid))
        # Says nothing about the older side: the list keeps its own cursor.
        self.assertNotIn("data-has-more", state)
        self.assertNotIn("data-first-uuid", state)
        # The groups are rendered inside the tail, whose children the client
        # merges before the live tail - at the end of the list.
        html = response.content.decode()
        tail = html[html.index('id="message-list-tail"') :]
        self.assertIn("numbered message 051", tail)

    def test_after_the_last_page_is_the_present(self):
        response = self.get(after=self.messages[100].uuid)
        self.assertEqual(_bodies(response), [f"{i:03d}" for i in range(102, COUNT + 1)])
        self.assertEqual(_state(response)["data-has-newer"], "false")

    def test_after_page_does_not_repeat_the_day_it_continues(self):
        # Every message is on 2024-03-01: the day's separator is already on
        # screen above the cursor.
        response = self.get(after=self.messages[49].uuid)
        self.assertNotIn('class="divider', response.content.decode())

    def test_older_page_carries_no_tail_and_nothing_about_the_newer_side(self):
        # The items of an older page are prepended whole: a tail in them would
        # put a second element with its id in the list.
        response = self.get(before=self.messages[60].uuid)
        self.assertNotIn('id="message-list-tail"', response.content.decode())
        state = _state(response)
        self.assertNotIn("data-has-newer", state)
        self.assertNotIn("data-last-uuid", state)

    def test_latest_page_is_at_the_present(self):
        response = self.get()
        self.assertEqual(_bodies(response)[-1], f"{COUNT:03d}")
        state = _state(response)
        self.assertEqual(state["data-has-newer"], "false")
        self.assertNotIn("data-target-uuid", state)
        self.assertIn('id="message-list-tail"', response.content.decode())

    def test_message_of_another_conversation_answers_like_an_unknown_one(self):
        for cursor in ("around", "after", "before"):
            with self.subTest(cursor=cursor):
                hidden = self.get(**{cursor: self.foreign_msg.uuid})
                absent = self.get(**{cursor: uuid.uuid4()})
                self.assertEqual(
                    (hidden.status_code, hidden.content),
                    (absent.status_code, absent.content),
                )

    def test_unknown_around_falls_back_to_the_latest_page_without_a_target(self):
        for value in (uuid.uuid4(), "not-a-uuid"):
            with self.subTest(value=value):
                response = self.get(around=value)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(_bodies(response)[-1], f"{COUNT:03d}")
                self.assertNotIn("data-target-uuid", _state(response))

    def test_non_member_is_refused_whatever_the_cursor(self):
        self.client.force_login(User.objects.create_user(username="eve-links"))
        hidden = self.get(around=self.messages[10].uuid)
        absent = self.get(around=uuid.uuid4())
        self.assertEqual(hidden.status_code, 403)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )


class ThreadReplyLinkTests(TestCase):
    """A reply is reached through its thread."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice-thread", password="x")
        cls.bob = User.objects.create_user(username="bob-thread", password="x")
        cls.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Threads", created_by=cls.alice
        )
        for user in (cls.alice, cls.bob):
            ConversationMember.objects.create(conversation=cls.conv, user=user)
        cls.flow = _numbered(cls.conv, cls.bob, 80)
        cls.root = cls.flow[9]  # 010
        cls.replies = []
        for i in range(1, 81):
            reply = Message.objects.create(
                conversation=cls.conv,
                author=cls.alice,
                body=f"thread reply {i:03d}",
                thread_root=cls.root,
            )
            Message.objects.filter(pk=reply.pk).update(
                created_at=BASE + timedelta(minutes=6 * 10 + i)
            )
            cls.replies.append(reply)

        other_root = cls.flow[20]
        cls.other_reply = Message.objects.create(
            conversation=cls.conv,
            author=cls.alice,
            body="elsewhere",
            thread_root=other_root,
        )

    def setUp(self):
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def test_flow_opens_on_the_root_and_names_the_thread(self):
        reply = self.replies[3]
        response = self.client.get(
            f"/chat/{self.conv.uuid}/messages", {"around": reply.uuid}
        )

        state = _state(response)
        self.assertEqual(state["data-target-uuid"], str(reply.uuid))
        self.assertEqual(state["data-target-thread-root"], str(self.root.uuid))
        # The reply is not in the flow; the page is cut around its root.
        self.assertIn("numbered message 010", response.content.decode())
        self.assertNotIn("thread reply 004", response.content.decode())

    def test_flow_opens_on_the_reply_itself_when_replies_are_inline(self):
        set_setting(
            self.alice, "chat", "preferences", {"showThreadRepliesInline": True}
        )
        reply = self.replies[3]
        response = self.client.get(
            f"/chat/{self.conv.uuid}/messages", {"around": reply.uuid}
        )

        self.assertIn("thread reply 004", response.content.decode())
        self.assertEqual(
            _state(response)["data-target-thread-root"], str(self.root.uuid)
        )

    def thread(self, **params):
        return self.client.get(f"/chat/threads/{self.root.uuid}/messages", params)

    def test_thread_opens_on_an_old_reply_with_its_root(self):
        reply = self.replies[4]  # 005, behind the latest 50 replies
        response = self.thread(around=reply.uuid)

        html = response.content.decode()
        self.assertIn("thread reply 005", html)
        self.assertIn('id="thread-root-message"', html)
        state = _state(response, "thread-message-list")
        self.assertEqual(state["data-target-uuid"], str(reply.uuid))
        self.assertEqual(state["data-has-newer"], "true")

    def test_thread_pages_forward_without_the_root(self):
        response = self.thread(after=self.replies[40].uuid)

        html = response.content.decode()
        self.assertNotIn('id="thread-root-message"', html)
        self.assertIn("thread reply 042", html)
        self.assertEqual(
            _state(response, "thread-message-list")["data-has-newer"], "false"
        )

    def test_reply_of_another_thread_answers_like_an_unknown_one(self):
        hidden = self.thread(around=self.other_reply.uuid)
        absent = self.thread(around=uuid.uuid4())
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

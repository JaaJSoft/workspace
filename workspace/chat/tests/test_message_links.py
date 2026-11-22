"""Message lists paged by the shared paginator, and links to one message.

The server-rendered lists count offsets from the oldest message, so a new
message never moves them. Without an offset a list opens on its last page, or
with ``?message=`` on the page holding that message, however old it is. A
message the reader cannot see - another conversation, another thread - answers
exactly like one that does not exist.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.message_lists import PAGE_SIZE
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


def _numbered(conversation, author, count):
    messages = []
    for i in range(1, count + 1):
        msg = Message.objects.create(
            conversation=conversation,
            author=author,
            body=f"numbered message {i:03d}",
        )
        # Strictly increasing, all on one day, and more than the five minutes
        # that would merge one author's messages into a group.
        Message.objects.filter(pk=msg.pk).update(
            created_at=BASE + timedelta(minutes=6 * i)
        )
        messages.append(msg)
    return messages


class MessageListPagingTests(TestCase):
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

        cls.foreign = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Elsewhere", created_by=cls.bob
        )
        ConversationMember.objects.create(conversation=cls.foreign, user=cls.bob)
        cls.foreign_msg = Message.objects.create(
            conversation=cls.foreign, author=cls.bob, body="foreign"
        )

    def setUp(self):
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def get(self, **params):
        return self.client.get(f"/chat/{self.conv.uuid}/messages", params)

    def test_without_an_offset_the_list_opens_on_its_last_page(self):
        response = self.get(limit=PAGE_SIZE)

        bodies = _bodies(response)
        self.assertEqual(
            bodies, [f"{i:03d}" for i in range(COUNT - PAGE_SIZE + 1, COUNT + 1)]
        )
        state = _state(response)
        self.assertEqual(state["data-offset"], str(COUNT - PAGE_SIZE))
        self.assertEqual(state["data-end"], str(COUNT))
        self.assertEqual(state["data-has-newer"], "false")
        self.assertNotIn("data-target-uuid", state)

    def test_without_a_limit_the_partial_still_renders_one_page(self):
        self.assertEqual(len(_bodies(self.get())), PAGE_SIZE)

    def test_an_offset_counts_from_the_oldest_message(self):
        response = self.get(limit=10, offset=20)

        self.assertEqual(_bodies(response), [f"{i:03d}" for i in range(21, 31)])
        state = _state(response)
        self.assertEqual(state["data-offset"], "20")
        self.assertEqual(state["data-end"], "30")
        self.assertEqual(state["data-has-newer"], "true")

    def test_a_new_message_does_not_move_the_offsets(self):
        before = _bodies(self.get(limit=10, offset=20))
        Message.objects.create(conversation=self.conv, author=self.bob, body="late")
        self.assertEqual(_bodies(self.get(limit=10, offset=20)), before)

    def test_a_link_opens_the_page_holding_an_old_message(self):
        target = self.messages[29]  # 030, far behind the last page
        response = self.get(limit=PAGE_SIZE, message=target.uuid)

        bodies = _bodies(response)
        half = PAGE_SIZE // 2
        self.assertEqual(bodies[0], f"{30 - half:03d}")
        self.assertEqual(len(bodies), PAGE_SIZE)
        self.assertIn("030", bodies)
        state = _state(response)
        self.assertEqual(state["data-offset"], str(29 - half))
        self.assertEqual(state["data-has-newer"], "true")
        self.assertEqual(state["data-target-uuid"], str(target.uuid))

    def test_a_link_to_the_first_message_opens_at_the_top(self):
        response = self.get(limit=PAGE_SIZE, message=self.messages[0].uuid)
        self.assertEqual(_bodies(response)[0], "001")
        self.assertEqual(_state(response)["data-offset"], "0")

    def test_an_offset_wins_over_a_link(self):
        response = self.get(limit=10, offset=0, message=self.messages[99].uuid)
        self.assertEqual(_bodies(response)[0], "001")

    def test_a_message_of_another_conversation_answers_like_an_unknown_one(self):
        hidden = self.get(limit=PAGE_SIZE, message=self.foreign_msg.uuid)
        absent = self.get(limit=PAGE_SIZE, message=uuid.uuid4())
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_a_malformed_link_opens_the_last_page(self):
        response = self.get(limit=PAGE_SIZE, message="not-a-uuid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_bodies(response)[-1], f"{COUNT:03d}")
        self.assertNotIn("data-target-uuid", _state(response))

    def test_a_non_member_is_refused_whatever_the_link(self):
        self.client.force_login(User.objects.create_user(username="eve-links"))
        hidden = self.get(message=self.messages[10].uuid)
        absent = self.get(message=uuid.uuid4())
        self.assertEqual(hidden.status_code, 403)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_day_separators_carry_their_day(self):
        # The client drops the second separator of a day split across pages.
        html = self.get(limit=5, offset=0).content.decode()
        self.assertIn('data-day="2024-03-01"', html)


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

        cls.other_reply = Message.objects.create(
            conversation=cls.conv,
            author=cls.alice,
            body="elsewhere",
            thread_root=cls.flow[20],
        )

    def setUp(self):
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def flow_page(self, **params):
        return self.client.get(f"/chat/{self.conv.uuid}/messages", params)

    def thread(self, **params):
        return self.client.get(f"/chat/threads/{self.root.uuid}/messages", params)

    def test_the_flow_opens_on_the_root_and_names_the_thread(self):
        reply = self.replies[3]
        response = self.flow_page(limit=PAGE_SIZE, message=reply.uuid)

        state = _state(response)
        self.assertEqual(state["data-target-uuid"], str(reply.uuid))
        self.assertEqual(state["data-target-thread-root"], str(self.root.uuid))
        html = response.content.decode()
        self.assertIn("numbered message 010", html)
        self.assertNotIn("thread reply 004", html)

    def test_the_flow_opens_on_the_reply_itself_when_replies_are_inline(self):
        set_setting(
            self.alice, "chat", "preferences", {"showThreadRepliesInline": True}
        )
        reply = self.replies[3]
        response = self.flow_page(limit=PAGE_SIZE, message=reply.uuid)

        self.assertIn("thread reply 004", response.content.decode())
        self.assertEqual(
            _state(response)["data-target-thread-root"], str(self.root.uuid)
        )

    def test_the_thread_opens_on_an_old_reply_with_its_root(self):
        reply = self.replies[4]  # 005, behind the last 50 replies
        response = self.thread(limit=PAGE_SIZE, message=reply.uuid)

        html = response.content.decode()
        self.assertIn("thread reply 005", html)
        self.assertIn('id="thread-root-message"', html)
        state = _state(response, "thread-message-list")
        self.assertEqual(state["data-target-uuid"], str(reply.uuid))
        self.assertEqual(state["data-offset"], "0")
        self.assertEqual(state["data-has-newer"], "true")

    def test_a_reply_of_another_thread_answers_like_an_unknown_one(self):
        hidden = self.thread(limit=PAGE_SIZE, message=self.other_reply.uuid)
        absent = self.thread(limit=PAGE_SIZE, message=uuid.uuid4())
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )


class MessageListApiTests(APITestCase):
    """GET /api/v1/chat/conversations/<id>/messages - the shared paginator."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice-api", password="x")
        cls.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="API", created_by=cls.alice
        )
        ConversationMember.objects.create(conversation=cls.conv, user=cls.alice)
        cls.messages = _numbered(cls.conv, cls.alice, 12)

    def setUp(self):
        self.client.force_authenticate(self.alice)

    def get(self, **params):
        return self.client.get(
            f"/api/v1/chat/conversations/{self.conv.uuid}/messages", params
        )

    def test_a_page_is_a_bare_array_newest_first_with_the_header(self):
        response = self.get(limit=5)

        self.assertEqual(
            [m["body"] for m in response.data],
            [f"numbered message {i:03d}" for i in range(12, 7, -1)],
        )
        self.assertEqual(response["X-Has-More"], "true")

    def test_the_offset_reaches_older_messages(self):
        response = self.get(limit=5, offset=10)

        self.assertEqual(
            [m["body"] for m in response.data],
            ["numbered message 002", "numbered message 001"],
        )
        self.assertEqual(response["X-Has-More"], "false")

    def test_without_a_limit_one_page_comes_back(self):
        # A conversation has no size limit: the default page bounds the answer.
        with patch("workspace.chat.views.messages.PAGE_SIZE", 5):
            response = self.get()
        self.assertEqual(len(response.data), 5)
        self.assertEqual(response["X-Has-More"], "true")


class MessageMarkersTests(TestCase):
    """What the server-rendered list marks on a message for the page's scripts."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")
        cls.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Team", created_by=cls.alice
        )
        for user in (cls.alice, cls.bob):
            ConversationMember.objects.create(conversation=cls.conv, user=user)

    def setUp(self):
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def _html(self):
        return self.client.get(f"/chat/{self.conv.uuid}/messages").content.decode()

    def test_an_edited_message_carries_the_marker_the_live_updates_look_for(self):
        # messages.js and sse.js append "(edited)" unless .edited-indicator is
        # already there: without the hook an edited message shows it twice.
        Message.objects.create(
            conversation=self.conv, author=self.bob, body="hi", edited_at=BASE
        )
        self.assertEqual(self._html().count("edited-indicator"), 1)

    def test_only_a_bot_error_offers_a_retry(self):
        Message.objects.create(
            conversation=self.conv, author=self.bob, body="\u26a0 careful with this"
        )
        self.assertNotIn("retryBotResponse", self._html())

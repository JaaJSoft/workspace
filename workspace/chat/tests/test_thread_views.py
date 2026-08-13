from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    PinnedMessage,
    ThreadParticipant,
)

User = get_user_model()


class ThreadMessagesViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.mallory = User.objects.create_user(username="mallory", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="the root message"
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="the threaded reply",
            reply_to=self.root,
            thread_root=self.root,
        )
        self.url = reverse(
            "chat_ui:thread_messages", kwargs={"root_uuid": self.root.uuid}
        )

    def tearDown(self):
        cache.clear()

    def test_the_thread_renders_the_root_and_its_replies(self):
        self.client.force_login(self.alice)
        html = self.client.get(self.url).content.decode()
        self.assertIn("the root message", html)
        self.assertIn("the threaded reply", html)

    def test_thread_message_ids_do_not_collide_with_the_main_flow(self):
        self.client.force_login(self.alice)
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'id="tmsg-{self.root.uuid}"', html)
        self.assertNotIn(f'id="msg-{self.root.uuid}"', html)

    def test_a_non_member_cannot_read_a_thread(self):
        self.client.force_login(self.mallory)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_panel_drops_the_quote_of_the_root_it_already_shows(self):
        self.client.force_login(self.alice)
        html = self.client.get(self.url).content.decode()
        # The reply answers the root, which the panel renders right above it.
        # Repeating it as a quote on every reply is noise.
        self.assertNotIn("the root message</p>", html)

    def test_the_panel_keeps_the_quote_when_a_reply_answers_another_reply(self):
        Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="answering the reply, not the root",
            reply_to=self.reply,
            thread_root=self.root,
        )
        self.client.force_login(self.alice)
        html = self.client.get(self.url).content.decode()
        self.assertIn("the threaded reply</p>", html)

    def test_the_main_flow_keeps_the_quote(self):
        self.client.force_login(self.alice)
        flow_url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )
        Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="an old-style inline reply",
            reply_to=self.root,
        )
        html = self.client.get(flow_url).content.decode()
        self.assertIn("the root message</p>", html)

    def test_a_malformed_cursor_still_shows_the_root(self):
        # A cursor that does not parse is ignored, so the response is the first
        # page - and the first page heads with the root.
        self.client.force_login(self.alice)
        html = self.client.get(self.url, {"before": "not-a-uuid"}).content.decode()
        self.assertIn("the root message", html)

    def test_an_unknown_cursor_still_shows_the_root(self):
        self.client.force_login(self.alice)
        stranger = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="elsewhere"
        )
        html = self.client.get(
            self.url, {"before": str(stranger.uuid)}
        ).content.decode()
        self.assertIn("the root message", html)

    def test_a_real_cursor_omits_the_root(self):
        # A genuine "load older" page prepends into a list that already shows
        # the root, so repeating it would duplicate the message.
        self.client.force_login(self.alice)
        html = self.client.get(
            self.url, {"before": str(self.reply.uuid)}
        ).content.decode()
        self.assertNotIn("the root message", html)

    def test_a_reply_is_not_a_thread_root(self):
        self.client.force_login(self.alice)
        url = reverse("chat_ui:thread_messages", kwargs={"root_uuid": self.reply.uuid})
        self.assertEqual(self.client.get(url).status_code, 404)


class ThreadReadViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        self.membership = ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice, unread_count=5
        )
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="root"
        )
        ThreadParticipant.objects.create(
            root_message=self.root, user=self.alice, unread_count=3
        )
        self.url = reverse("chat-thread-read", kwargs={"root_uuid": self.root.uuid})

    def tearDown(self):
        cache.clear()

    def test_reading_a_thread_clears_its_share_of_the_conversation_badge(self):
        self.client.force_login(self.alice)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["cleared"], 3)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.unread_count, 2)
        participant = ThreadParticipant.objects.get(
            root_message=self.root, user=self.alice
        )
        self.assertEqual(participant.unread_count, 0)

    def test_reading_the_same_thread_twice_only_clears_it_once(self):
        # The second call finds the counter already at zero, so it must report
        # nothing cleared and leave the conversation badge where the first
        # call put it.
        self.client.force_login(self.alice)
        first = self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(first.json()["cleared"], 3)
        self.assertEqual(second.json()["cleared"], 0)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.unread_count, 2)

    def test_the_conversation_badge_never_goes_negative(self):
        ConversationMember.objects.filter(pk=self.membership.pk).update(unread_count=1)
        self.client.force_login(self.alice)
        self.client.post(self.url)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.unread_count, 0)


class ThreadSearchTests(TestCase):
    """A search hit inside a thread has to say so, or the UI cannot reach it."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="needle in the root"
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="needle in a reply",
            reply_to=self.root,
            thread_root=self.root,
        )
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def _search(self):
        url = reverse(
            "chat-message-search",
            kwargs={"conversation_id": self.conversation.uuid},
        )
        return {
            r["body"]: r
            for r in self.client.get(url, {"q": "needle"}).json()["results"]
        }

    def test_a_threaded_hit_carries_its_thread_root(self):
        results = self._search()
        self.assertEqual(
            results["needle in a reply"]["thread_root"], str(self.root.uuid)
        )

    def test_a_main_flow_hit_carries_no_thread_root(self):
        results = self._search()
        self.assertIsNone(results["needle in the root"]["thread_root"])


class ThreadPanelRenderingTests(TestCase):
    """Structure of the rendered panel: pagination, deletions, pins."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="the root message"
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="the threaded reply",
            reply_to=self.root,
            thread_root=self.root,
        )
        self.url = reverse(
            "chat_ui:thread_messages", kwargs={"root_uuid": self.root.uuid}
        )
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def test_the_root_renders_outside_the_paginated_list(self):
        # "Load older" prepends fetched content into #thread-message-list, so a
        # root inside it would sink below the older replies it prepends.
        html = self.client.get(self.url).content.decode()
        list_part = html.split('id="thread-message-list"', 1)[1]
        self.assertNotIn(f'id="tmsg-{self.root.uuid}"', list_part)
        self.assertIn(f'id="tmsg-{self.root.uuid}"', html)

    def test_an_older_page_carries_no_root_block(self):
        html = self.client.get(
            self.url, {"before": str(self.reply.uuid)}
        ).content.decode()
        self.assertNotIn('id="thread-root-message"', html)

    def test_a_deleted_reply_is_not_rendered(self):
        # The footer advertises recount_thread's number, which counts live
        # replies only - the panel must show that many, not placeholders.
        self.reply.deleted_at = timezone.now()
        self.reply.save(update_fields=["deleted_at"])
        html = self.client.get(self.url).content.decode()
        self.assertNotIn(f'id="tmsg-{self.reply.uuid}"', html)

    def test_a_pinned_reply_keeps_its_marker(self):
        PinnedMessage.objects.create(
            conversation=self.conversation, message=self.reply, pinned_by=self.alice
        )
        resp = self.client.get(self.url)
        self.assertIn(self.reply.uuid, resp.context["pinned_message_ids"])


class MarkReadThreadInteractionTests(TestCase):
    """The conversation badge and the thread counters denormalise one number,
    so every path that zeroes one half must zero the other."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.bob = User.objects.create_user(username="bob", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        self.membership = ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.bob)
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="root"
        )
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def _post_as_bob(self, body, reply_to=None):
        api = APIClient()
        api.force_authenticate(user=self.bob)
        payload = {"body": body}
        if reply_to is not None:
            payload["reply_to_uuid"] = str(reply_to.uuid)
        api.post(
            reverse(
                "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
            ),
            payload,
            format="json",
        )

    def _mark_conversation_read(self):
        return self.client.post(
            reverse(
                "chat-mark-read", kwargs={"conversation_id": self.conversation.uuid}
            )
        )

    def _badge(self):
        self.membership.refresh_from_db()
        return self.membership.unread_count

    def test_marking_the_conversation_read_settles_the_thread_counters(self):
        self._post_as_bob("a reply", reply_to=self.root)
        self._mark_conversation_read()
        participant = ThreadParticipant.objects.get(
            root_message=self.root, user=self.alice
        )
        self.assertEqual(participant.unread_count, 0)
        self.assertIsNotNone(participant.last_read_at)

    def test_an_old_thread_backlog_cannot_eat_later_unread(self):
        # Replies arrive, the user opens the conversation (mark read), five
        # main-flow messages follow, and only then the old thread is opened:
        # its stale backlog must not be subtracted from a badge that already
        # dropped it.
        for i in range(3):
            self._post_as_bob(f"reply {i}", reply_to=self.root)
        self._mark_conversation_read()
        for i in range(5):
            self._post_as_bob(f"main {i}")
        self.assertEqual(self._badge(), 5)

        resp = self.client.post(
            reverse("chat-thread-read", kwargs={"root_uuid": self.root.uuid})
        )

        self.assertEqual(resp.json()["cleared"], 0)
        self.assertEqual(self._badge(), 5)

    def test_rejoining_a_conversation_restarts_the_thread_counters(self):
        # Rejoining resets the badge to zero; a stale thread backlog surviving
        # it would later be subtracted from unread the user never saw.
        self._post_as_bob("a reply", reply_to=self.root)
        ConversationMember.objects.filter(pk=self.membership.pk).update(
            left_at=timezone.now()
        )

        api = APIClient()
        api.force_authenticate(user=self.bob)
        api.post(
            reverse(
                "chat-conversation-members",
                kwargs={"conversation_id": self.conversation.uuid},
            ),
            {"user_ids": [self.alice.id]},
            format="json",
        )

        participant = ThreadParticipant.objects.get(
            root_message=self.root, user=self.alice
        )
        self.assertEqual(participant.unread_count, 0)
        self.assertEqual(self._badge(), 0)


class QuoteIntoThreadTests(TestCase):
    """A main-flow quote pointing at a threaded reply must carry the thread
    root, or the click pages the flow back forever hunting a message that is
    not there."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="the root"
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="a threaded reply",
            reply_to=self.root,
            thread_root=self.root,
        )
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()

    def _flow_html(self):
        return self.client.get(
            reverse(
                "chat_ui:conversation_messages",
                kwargs={"conversation_uuid": self.conversation.uuid},
            )
        ).content.decode()

    def test_a_quote_of_a_threaded_reply_carries_the_thread_root(self):
        Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="quoting the threaded reply from the flow",
            reply_to=self.reply,
        )
        self.assertIn(
            f"scrollToMessage('{self.reply.uuid}', '{self.root.uuid}')",
            self._flow_html(),
        )

    def test_a_quote_of_a_main_flow_message_carries_none(self):
        Message.objects.create(
            conversation=self.conversation,
            author=self.alice,
            body="quoting the root from the flow",
            reply_to=self.root,
        )
        self.assertIn(f"scrollToMessage('{self.root.uuid}')", self._flow_html())

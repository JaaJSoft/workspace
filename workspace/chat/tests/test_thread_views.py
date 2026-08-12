from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
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

    def test_the_conversation_badge_never_goes_negative(self):
        ConversationMember.objects.filter(pk=self.membership.pk).update(unread_count=1)
        self.client.force_login(self.alice)
        self.client.post(self.url)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.unread_count, 0)

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    ThreadParticipant,
)

User = get_user_model()


class ThreadPostingTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.carol = User.objects.create_user(username="carol", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        for user in (self.alice, self.bob, self.carol):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="root"
        )
        self.client = APIClient()

    def _post(self, user, body, reply_to=None):
        self.client.force_authenticate(user=user)
        payload = {"body": body}
        if reply_to is not None:
            payload["reply_to_uuid"] = str(reply_to.uuid)
        url = reverse(
            "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
        )
        return self.client.post(url, payload, format="json")

    def _member(self, user):
        return ConversationMember.objects.get(conversation=self.conversation, user=user)

    def test_a_reply_is_anchored_to_the_thread_root(self):
        resp = self._post(self.bob, "reply", reply_to=self.root)
        self.assertEqual(resp.status_code, 201)
        reply = Message.objects.get(uuid=resp.data["uuid"])
        self.assertEqual(reply.thread_root_id, self.root.uuid)
        self.assertEqual(reply.reply_to_id, self.root.uuid)

    def test_a_reply_to_a_reply_joins_the_same_thread(self):
        first = self._post(self.bob, "reply", reply_to=self.root)
        first_msg = Message.objects.get(uuid=first.data["uuid"])
        second = self._post(self.carol, "reply to reply", reply_to=first_msg)
        second_msg = Message.objects.get(uuid=second.data["uuid"])
        self.assertEqual(second_msg.thread_root_id, self.root.uuid)
        self.assertEqual(second_msg.reply_to_id, first_msg.uuid)

    def test_replying_maintains_the_root_counter(self):
        self._post(self.bob, "reply", reply_to=self.root)
        self._post(self.carol, "another", reply_to=self.root)
        self.root.refresh_from_db()
        self.assertEqual(self.root.reply_count, 2)
        self.assertIsNotNone(self.root.last_reply_at)

    def test_the_root_author_and_the_replier_become_participants(self):
        self._post(self.bob, "reply", reply_to=self.root)
        participants = set(
            ThreadParticipant.objects.filter(root_message=self.root).values_list(
                "user_id", flat=True
            )
        )
        self.assertEqual(participants, {self.alice.id, self.bob.id})

    def test_a_thread_reply_only_moves_the_badge_of_participants(self):
        self._post(self.bob, "reply", reply_to=self.root)
        # alice authored the root, so she is a participant and gets the badge.
        self.assertEqual(self._member(self.alice).unread_count, 1)
        # carol is a member of the conversation but not of the thread.
        self.assertEqual(self._member(self.carol).unread_count, 0)

    def test_a_plain_message_still_moves_every_members_badge(self):
        self._post(self.bob, "hello everyone")
        self.assertEqual(self._member(self.alice).unread_count, 1)
        self.assertEqual(self._member(self.carol).unread_count, 1)

    def test_the_serializer_exposes_the_thread_anchor(self):
        resp = self._post(self.bob, "reply", reply_to=self.root)
        self.assertEqual(resp.data["thread_root"], str(self.root.uuid))

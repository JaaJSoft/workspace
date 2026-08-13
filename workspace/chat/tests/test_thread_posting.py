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

    def test_last_reply_at_is_the_reply_own_timestamp(self):
        resp = self._post(self.bob, "reply", reply_to=self.root)
        reply = Message.objects.get(uuid=resp.data["uuid"])
        self.root.refresh_from_db()
        self.assertEqual(self.root.last_reply_at, reply.created_at)


class ThreadDeletionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="root"
        )
        self.client = APIClient()

    def _reply(self, body):
        self.client.force_authenticate(user=self.bob)
        resp = self.client.post(
            reverse(
                "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
            ),
            {"body": body, "reply_to_uuid": str(self.root.uuid)},
            format="json",
        )
        return Message.objects.get(uuid=resp.data["uuid"])

    def _delete(self, message, user):
        self.client.force_authenticate(user=user)
        return self.client.delete(
            reverse(
                "chat-message-detail",
                kwargs={
                    "conversation_id": self.conversation.uuid,
                    "message_id": message.uuid,
                },
            )
        )

    def test_deleting_the_latest_reply_rewinds_the_root_counters(self):
        first = self._reply("first")
        second = self._reply("second")

        self.assertEqual(self._delete(second, self.bob).status_code, 204)

        self.root.refresh_from_db()
        self.assertEqual(self.root.reply_count, 1)
        self.assertEqual(self.root.last_reply_at, first.created_at)

    def test_deleting_the_final_reply_empties_the_thread(self):
        only = self._reply("only")

        self.assertEqual(self._delete(only, self.bob).status_code, 204)

        self.root.refresh_from_db()
        self.assertEqual(self.root.reply_count, 0)
        self.assertIsNone(self.root.last_reply_at)

    def test_deleting_a_middle_reply_keeps_the_latest_timestamp(self):
        first = self._reply("first")
        second = self._reply("second")

        self._delete(first, self.bob)

        self.root.refresh_from_db()
        self.assertEqual(self.root.reply_count, 1)
        self.assertEqual(self.root.last_reply_at, second.created_at)

    def test_deleting_the_root_leaves_its_own_counters_alone(self):
        reply = self._reply("a reply")

        self.assertEqual(self._delete(self.root, self.alice).status_code, 204)

        self.root.refresh_from_db()
        self.assertEqual(self.root.reply_count, 1)
        self.assertEqual(self.root.last_reply_at, reply.created_at)


class ThreadReplyDeletionAccountingTests(TestCase):
    """Deleting a reply must retract exactly the unread it delivered.

    Delivery moves the counters of the thread's participants only, so the
    deletion may only move those back - and both halves (the participant's
    thread counter and their conversation badge) must move together.
    """

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
        resp = self.client.post(
            reverse(
                "chat-messages", kwargs={"conversation_id": self.conversation.uuid}
            ),
            payload,
            format="json",
        )
        return Message.objects.get(uuid=resp.data["uuid"])

    def _delete(self, message, user):
        self.client.force_authenticate(user=user)
        return self.client.delete(
            reverse(
                "chat-message-detail",
                kwargs={
                    "conversation_id": self.conversation.uuid,
                    "message_id": message.uuid,
                },
            )
        )

    def _badge(self, user):
        return ConversationMember.objects.get(
            conversation=self.conversation, user=user
        ).unread_count

    def _thread_counter(self, user):
        participant = ThreadParticipant.objects.filter(
            root_message=self.root, user=user
        ).first()
        return participant.unread_count if participant else 0

    def _read_thread(self, user):
        self.client.force_authenticate(user=user)
        return self.client.post(
            reverse("chat-thread-read", kwargs={"root_uuid": self.root.uuid})
        )

    def test_deleting_a_reply_leaves_non_participants_badges_alone(self):
        # carol is not in the thread: the reply never moved her badge, only the
        # main-flow message did - so the deletion must not move it either.
        self._post(self.bob, "a main-flow message")
        reply = self._post(self.bob, "reply", reply_to=self.root)
        self.assertEqual(self._badge(self.carol), 1)

        self._delete(reply, self.bob)

        self.assertEqual(self._badge(self.carol), 1)

    def test_deleting_a_reply_rewinds_the_participants_counters_together(self):
        reply = self._post(self.bob, "reply", reply_to=self.root)
        self.assertEqual(self._badge(self.alice), 1)
        self.assertEqual(self._thread_counter(self.alice), 1)

        self._delete(reply, self.bob)

        self.assertEqual(self._badge(self.alice), 0)
        self.assertEqual(self._thread_counter(self.alice), 0)

    def test_a_deletion_then_a_thread_read_does_not_double_subtract(self):
        # The deleted reply must not leave a phantom +1 on the thread counter
        # that a later read subtracts from a badge that dropped it already.
        reply = self._post(self.bob, "reply", reply_to=self.root)
        self._post(self.bob, "a main-flow message")
        self.assertEqual(self._badge(self.alice), 2)

        self._delete(reply, self.bob)
        resp = self._read_thread(self.alice)

        self.assertEqual(resp.json()["cleared"], 0)
        self.assertEqual(self._badge(self.alice), 1)

    def test_deleting_an_already_read_reply_does_not_move_the_badge(self):
        reply = self._post(self.bob, "reply", reply_to=self.root)
        self._read_thread(self.alice)
        self._post(self.bob, "a main-flow message")
        self.assertEqual(self._badge(self.alice), 1)

        self._delete(reply, self.bob)

        self.assertEqual(self._badge(self.alice), 1)
        self.assertEqual(self._thread_counter(self.alice), 0)


class ThreadNotificationWiringTests(TestCase):
    """deliver_message must hand the thread anchor to the notification."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="root"
        )
        self.client = APIClient()

    def test_a_posted_reply_notifies_with_the_thread_deep_link(self):
        from unittest.mock import patch

        self.client.force_authenticate(user=self.bob)
        with (
            patch(
                "workspace.notifications.services.notifications.notify_stream"
            ) as notify_stream,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(
                reverse(
                    "chat-messages",
                    kwargs={"conversation_id": self.conversation.uuid},
                ),
                {"body": "a reply", "reply_to_uuid": str(self.root.uuid)},
                format="json",
            )
        self.assertEqual(
            notify_stream.call_args.kwargs["url"],
            f"/chat/{self.conversation.uuid}?thread={self.root.uuid}",
        )

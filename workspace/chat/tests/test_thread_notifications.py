from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.notifications import notify_new_message
from workspace.chat.services.threads import ensure_participants

User = get_user_model()


class ThreadNotificationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.carol = User.objects.create_user(username="carol", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice, title="Team"
        )
        for user in (self.alice, self.bob, self.carol):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.root = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="root"
        )
        ensure_participants(self.root, [self.alice.id, self.bob.id])

    def _recipients(self, mock_notify):
        self.assertTrue(mock_notify.called)
        return set(mock_notify.call_args.kwargs["recipient_ids"])

    @patch("workspace.notifications.services.notifications.notify_stream")
    def test_a_thread_reply_notifies_participants_only(self, notify_stream):
        notify_new_message(
            self.conversation,
            self.bob,
            "a reply",
            thread_recipient_ids={self.alice.id},
        )
        self.assertEqual(self._recipients(notify_stream), {self.alice.id})

    @patch("workspace.notifications.services.notifications.notify_stream")
    def test_a_plain_message_still_notifies_every_member(self, notify_stream):
        notify_new_message(self.conversation, self.bob, "hello")
        self.assertEqual(
            self._recipients(notify_stream), {self.alice.id, self.carol.id}
        )

    @patch("workspace.notifications.services.notifications.notify_stream")
    def test_silencing_a_conversation_silences_its_threads(self, notify_stream):
        ConversationMember.objects.filter(
            conversation=self.conversation, user=self.alice
        ).update(notification_level=ConversationMember.NotificationLevel.NONE)
        notify_new_message(
            self.conversation,
            self.bob,
            "a reply",
            thread_recipient_ids={self.alice.id},
        )
        self.assertFalse(notify_stream.called)

    @patch("workspace.notifications.services.notifications.notify_stream")
    def test_a_mention_reaches_a_non_participant(self, notify_stream):
        notify_new_message(
            self.conversation,
            self.bob,
            "hey @carol",
            mentioned_user_ids={self.carol.id},
            thread_recipient_ids={self.alice.id},
        )
        self.assertEqual(
            self._recipients(notify_stream), {self.alice.id, self.carol.id}
        )

    @patch("workspace.notifications.services.notifications.notify_stream")
    def test_a_thread_reply_deep_links_to_its_thread(self, notify_stream):
        # By default the reply is not in the main flow, so a notification
        # landing on the bare conversation would show nothing new.
        notify_new_message(
            self.conversation,
            self.bob,
            "a reply",
            thread_recipient_ids={self.alice.id},
            thread_root_id=self.root.uuid,
        )
        self.assertEqual(
            notify_stream.call_args.kwargs["url"],
            f"/chat/{self.conversation.uuid}?thread={self.root.uuid}",
        )

    @patch("workspace.notifications.services.notifications.notify_stream")
    def test_a_plain_message_links_to_the_conversation(self, notify_stream):
        notify_new_message(self.conversation, self.bob, "hello")
        self.assertEqual(
            notify_stream.call_args.kwargs["url"], f"/chat/{self.conversation.uuid}"
        )

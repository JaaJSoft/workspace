from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from workspace.chat.models import (
    Conversation,
    Message,
    ThreadParticipant,
)

User = get_user_model()


class ThreadSchemaTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )

    def _message(self, body, **kwargs):
        return Message.objects.create(
            conversation=self.conversation, author=self.alice, body=body, **kwargs
        )

    def test_a_new_message_is_its_own_flow_entry(self):
        root = self._message("root")
        self.assertIsNone(root.thread_root)
        self.assertEqual(root.reply_count, 0)
        self.assertIsNone(root.last_reply_at)

    def test_a_reply_points_at_its_thread_root(self):
        root = self._message("root")
        reply = self._message("reply", thread_root=root, reply_to=root)
        self.assertEqual(list(root.thread_replies.all()), [reply])

    def test_the_main_flow_excludes_replies(self):
        root = self._message("root")
        self._message("reply", thread_root=root, reply_to=root)
        flow = Message.objects.filter(
            conversation=self.conversation, thread_root__isnull=True
        )
        self.assertEqual([m.body for m in flow], ["root"])

    def test_a_user_participates_in_a_thread_only_once(self):
        root = self._message("root")
        ThreadParticipant.objects.create(root_message=root, user=self.alice)
        with self.assertRaises(IntegrityError):
            ThreadParticipant.objects.create(root_message=root, user=self.alice)

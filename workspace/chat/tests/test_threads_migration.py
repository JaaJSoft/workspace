from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    ThreadParticipant,
)
from workspace.chat.services.threads import backfill_threads

User = get_user_model()


class BackfillThreadsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.conversation, user=user)

    def _message(self, author, body, **kwargs):
        return Message.objects.create(
            conversation=self.conversation, author=author, body=body, **kwargs
        )

    def _backfill(self):
        backfill_threads(Message, ThreadParticipant, ConversationMember)

    def test_a_chain_of_three_collapses_onto_one_root(self):
        root = self._message(self.alice, "root")
        first = self._message(self.bob, "first", reply_to=root)
        second = self._message(self.alice, "second", reply_to=first)

        self._backfill()

        first.refresh_from_db()
        second.refresh_from_db()
        root.refresh_from_db()
        self.assertEqual(first.thread_root_id, root.uuid)
        self.assertEqual(second.thread_root_id, root.uuid)
        self.assertIsNone(root.thread_root_id)
        self.assertEqual(root.reply_count, 2)
        self.assertEqual(root.last_reply_at, second.created_at)

    def test_every_author_in_the_chain_becomes_a_participant(self):
        root = self._message(self.alice, "root")
        self._message(self.bob, "first", reply_to=root)

        self._backfill()

        self.assertEqual(
            set(
                ThreadParticipant.objects.filter(root_message=root).values_list(
                    "user_id", flat=True
                )
            ),
            {self.alice.id, self.bob.id},
        )

    def test_a_message_with_no_reply_gets_no_thread_and_no_participants(self):
        lone = self._message(self.alice, "lone")

        self._backfill()

        lone.refresh_from_db()
        self.assertIsNone(lone.thread_root_id)
        self.assertEqual(lone.reply_count, 0)
        self.assertFalse(ThreadParticipant.objects.filter(root_message=lone).exists())

    def test_a_cycle_does_not_hang_and_leaves_the_row_unthreaded(self):
        a = self._message(self.alice, "a")
        b = self._message(self.bob, "b", reply_to=a)
        Message.objects.filter(pk=a.pk).update(reply_to=b)

        self._backfill()

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNone(a.thread_root_id)
        self.assertIsNone(b.thread_root_id)

    def test_the_backfill_is_idempotent(self):
        root = self._message(self.alice, "root")
        self._message(self.bob, "first", reply_to=root)

        self._backfill()
        self._backfill()

        root.refresh_from_db()
        self.assertEqual(root.reply_count, 1)
        self.assertEqual(ThreadParticipant.objects.filter(root_message=root).count(), 2)

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from workspace.chat.models import (
    Conversation,
    Message,
    ThreadParticipant,
)
from workspace.chat.services.threads import (
    ensure_participants,
    mark_thread_read,
    participant_user_ids,
    resolve_thread_root,
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


class ThreadServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )

    def _message(self, body, **kwargs):
        return Message.objects.create(
            conversation=self.conversation, author=self.alice, body=body, **kwargs
        )

    def test_replying_to_a_plain_message_makes_it_the_root(self):
        root = self._message("root")
        self.assertEqual(resolve_thread_root(root), root)

    def test_replying_to_a_reply_flattens_onto_the_same_root(self):
        root = self._message("root")
        reply = self._message("reply", thread_root=root, reply_to=root)
        self.assertEqual(resolve_thread_root(reply), root)

    def test_ensuring_participants_is_idempotent(self):
        root = self._message("root")
        ensure_participants(root, [self.alice.id, self.bob.id])
        ensure_participants(root, [self.alice.id, self.bob.id])
        self.assertEqual(participant_user_ids(root), {self.alice.id, self.bob.id})

    def test_marking_a_thread_read_reports_and_clears_the_backlog(self):
        root = self._message("root")
        ensure_participants(root, [self.bob.id])
        ThreadParticipant.objects.filter(root_message=root, user=self.bob).update(
            unread_count=3
        )
        cleared = mark_thread_read(root, self.bob)
        self.assertEqual(cleared, 3)
        participant = ThreadParticipant.objects.get(root_message=root, user=self.bob)
        self.assertEqual(participant.unread_count, 0)
        self.assertIsNotNone(participant.last_read_at)

    def test_marking_a_thread_read_for_a_non_participant_clears_nothing(self):
        root = self._message("root")
        self.assertEqual(mark_thread_read(root, self.bob), 0)

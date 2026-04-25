from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.conversations import get_active_membership, get_or_create_dm, get_unread_counts, \
    user_conversation_ids

User = get_user_model()


class ChatAuthzMixin:
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')

    def _make_conversation(self, kind=Conversation.Kind.GROUP, members=None):
        conv = Conversation.objects.create(kind=kind, created_by=self.alice)
        for user in (members or []):
            ConversationMember.objects.create(conversation=conv, user=user)
        return conv


# ── user_conversation_ids ───────────────────────────────────────

class UserConversationIdsTests(ChatAuthzMixin, TestCase):

    def test_returns_conversations_user_is_active_member_of(self):
        conv = self._make_conversation(members=[self.alice, self.bob])
        ids = list(user_conversation_ids(self.alice))
        self.assertIn(conv.pk, ids)

    def test_excludes_conversations_user_left(self):
        conv = self._make_conversation(members=[self.alice, self.bob])
        ConversationMember.objects.filter(
            conversation=conv, user=self.alice,
        ).update(left_at=timezone.now())
        ids = list(user_conversation_ids(self.alice))
        self.assertNotIn(conv.pk, ids)

    def test_excludes_conversations_user_is_not_member_of(self):
        conv = self._make_conversation(members=[self.bob])
        ids = list(user_conversation_ids(self.alice))
        self.assertNotIn(conv.pk, ids)

    def test_returns_empty_for_user_with_no_conversations(self):
        carol = User.objects.create_user(username='carol', password='pass')
        ids = list(user_conversation_ids(carol))
        self.assertEqual(ids, [])

    def test_returns_multiple_conversations(self):
        conv1 = self._make_conversation(members=[self.alice])
        conv2 = self._make_conversation(members=[self.alice])
        ids = list(user_conversation_ids(self.alice))
        self.assertEqual(set(ids), {conv1.pk, conv2.pk})


# ── get_active_membership ───────────────────────────────────────

class GetActiveMembershipTests(ChatAuthzMixin, TestCase):

    def test_returns_membership_for_active_member(self):
        conv = self._make_conversation(members=[self.alice])
        membership = get_active_membership(self.alice, conv.pk)
        self.assertIsNotNone(membership)
        self.assertEqual(membership.user, self.alice)
        self.assertEqual(membership.conversation, conv)

    def test_returns_none_for_non_member(self):
        conv = self._make_conversation(members=[self.bob])
        self.assertIsNone(get_active_membership(self.alice, conv.pk))

    def test_returns_none_for_user_who_left(self):
        conv = self._make_conversation(members=[self.alice])
        ConversationMember.objects.filter(
            conversation=conv, user=self.alice,
        ).update(left_at=timezone.now())
        self.assertIsNone(get_active_membership(self.alice, conv.pk))

    def test_returns_none_for_nonexistent_conversation(self):
        import uuid
        self.assertIsNone(get_active_membership(self.alice, uuid.uuid4()))


# ── get_or_create_dm ────────────────────────────────────────────

class GetOrCreateDmTests(ChatAuthzMixin, TestCase):

    def test_creates_new_dm(self):
        conv = get_or_create_dm(self.alice, self.bob)
        self.assertEqual(conv.kind, Conversation.Kind.DM)
        self.assertEqual(conv.members.count(), 2)
        member_users = set(conv.members.values_list('user_id', flat=True))
        self.assertEqual(member_users, {self.alice.pk, self.bob.pk})

    def test_returns_existing_dm(self):
        conv1 = get_or_create_dm(self.alice, self.bob)
        conv2 = get_or_create_dm(self.alice, self.bob)
        self.assertEqual(conv1.pk, conv2.pk)

    def test_symmetric_lookup(self):
        conv1 = get_or_create_dm(self.alice, self.bob)
        conv2 = get_or_create_dm(self.bob, self.alice)
        self.assertEqual(conv1.pk, conv2.pk)

    def test_reactivates_member_who_left(self):
        conv = get_or_create_dm(self.alice, self.bob)
        ConversationMember.objects.filter(
            conversation=conv, user=self.alice,
        ).update(left_at=timezone.now())

        conv2 = get_or_create_dm(self.alice, self.bob)
        self.assertEqual(conv.pk, conv2.pk)
        membership = ConversationMember.objects.get(
            conversation=conv, user=self.alice,
        )
        self.assertIsNone(membership.left_at)

    def test_different_pairs_create_different_dms(self):
        carol = User.objects.create_user(username='carol', password='pass')
        conv1 = get_or_create_dm(self.alice, self.bob)
        conv2 = get_or_create_dm(self.alice, carol)
        self.assertNotEqual(conv1.pk, conv2.pk)


# ── get_unread_counts ───────────────────────────────────────────

class GetUnreadCountsTests(ChatAuthzMixin, TestCase):

    def test_returns_zero_total_when_no_unread(self):
        self._make_conversation(members=[self.alice])
        counts = get_unread_counts(self.alice)
        self.assertEqual(counts['total'], 0)
        self.assertEqual(counts['conversations'], {})

    def test_returns_unread_counts(self):
        conv = self._make_conversation(members=[self.alice])
        ConversationMember.objects.filter(
            conversation=conv, user=self.alice,
        ).update(unread_count=5)
        counts = get_unread_counts(self.alice)
        self.assertEqual(counts['total'], 5)
        self.assertEqual(counts['conversations'][str(conv.pk)], 5)

    def test_sums_across_conversations(self):
        conv1 = self._make_conversation(members=[self.alice])
        conv2 = self._make_conversation(members=[self.alice])
        ConversationMember.objects.filter(
            conversation=conv1, user=self.alice,
        ).update(unread_count=3)
        ConversationMember.objects.filter(
            conversation=conv2, user=self.alice,
        ).update(unread_count=2)
        counts = get_unread_counts(self.alice)
        self.assertEqual(counts['total'], 5)

    def test_excludes_left_conversations(self):
        conv = self._make_conversation(members=[self.alice])
        ConversationMember.objects.filter(
            conversation=conv, user=self.alice,
        ).update(unread_count=3, left_at=timezone.now())
        counts = get_unread_counts(self.alice)
        self.assertEqual(counts['total'], 0)

    def test_excludes_other_users_unreads(self):
        conv = self._make_conversation(members=[self.alice, self.bob])
        ConversationMember.objects.filter(
            conversation=conv, user=self.bob,
        ).update(unread_count=10)
        counts = get_unread_counts(self.alice)
        self.assertEqual(counts['total'], 0)

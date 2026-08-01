from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from workspace.ai.models import BotProfile
from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.serializers import ConversationDetailSerializer
from workspace.chat.services.group_sync import (
    create_group_conversation,
    resync_conversation_members,
)


class ConversationGroupsModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.team = Group.objects.create(name="Team A")

    def test_conversation_can_attach_groups(self):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Chan", created_by=self.user
        )
        conv.groups.add(self.team)
        self.assertEqual(list(self.team.conversations.all()), [conv])

    def test_detail_serializer_exposes_groups(self):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Chan", created_by=self.user
        )
        conv.groups.add(self.team)
        data = ConversationDetailSerializer(conv).data
        self.assertEqual(data["groups"], [{"id": self.team.pk, "name": "Team A"}])


class CreateGroupConversationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@test.com", password="pass123"
        )
        self.eve = User.objects.create_user(
            username="eve", email="eve@test.com", password="pass123"
        )
        self.inactive = User.objects.create_user(
            username="ghost",
            email="ghost@test.com",
            password="pass123",
            is_active=False,
        )
        self.team_a = Group.objects.create(name="Team A")
        self.team_b = Group.objects.create(name="Team B")
        self.alice.groups.add(self.team_a)
        self.bob.groups.add(self.team_a)
        self.eve.groups.add(self.team_b)
        self.inactive.groups.add(self.team_a)

    def test_creates_conversation_with_union_of_active_group_users(self):
        conv = create_group_conversation(
            self.alice, [self.team_a, self.team_b], title="Chan"
        )
        self.assertEqual(conv.kind, Conversation.Kind.GROUP)
        self.assertEqual(set(conv.groups.all()), {self.team_a, self.team_b})
        active = ConversationMember.objects.filter(
            conversation=conv, left_at__isnull=True
        )
        self.assertEqual(
            {m.user_id for m in active}, {self.alice.id, self.bob.id, self.eve.id}
        )

    def test_blank_title_falls_back_to_first_group_name(self):
        conv = create_group_conversation(self.alice, [self.team_a])
        self.assertEqual(conv.title, "Team A")

    def test_creator_must_belong_to_at_least_one_group(self):
        with self.assertRaises(PermissionDenied):
            create_group_conversation(self.alice, [self.team_b])
        # eve is in team_b, so attaching team_a on top is allowed
        conv = create_group_conversation(self.eve, [self.team_b, self.team_a])
        self.assertEqual(conv.groups.count(), 2)


class ResyncConversationMembersTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@test.com", password="pass123"
        )
        self.team_a = Group.objects.create(name="Team A")
        self.team_b = Group.objects.create(name="Team B")
        self.alice.groups.add(self.team_a)
        self.bob.groups.add(self.team_a)
        self.conv = create_group_conversation(self.alice, [self.team_a], title="Chan")

    def _active_ids(self):
        return set(
            ConversationMember.objects.filter(
                conversation=self.conv, left_at__isnull=True
            ).values_list("user_id", flat=True)
        )

    def test_uncovered_member_is_soft_deactivated(self):
        self.bob.groups.clear()
        resync_conversation_members(self.conv)
        self.assertEqual(self._active_ids(), {self.alice.id})
        row = ConversationMember.objects.get(conversation=self.conv, user=self.bob)
        self.assertIsNotNone(row.left_at)

    def test_rejoining_reactivates_the_same_row_and_resets_unread(self):
        original = ConversationMember.objects.get(conversation=self.conv, user=self.bob)
        self.bob.groups.clear()
        resync_conversation_members(self.conv)
        self.bob.groups.add(self.team_a)
        resync_conversation_members(self.conv)
        row = ConversationMember.objects.get(conversation=self.conv, user=self.bob)
        self.assertEqual(row.uuid, original.uuid)
        self.assertIsNone(row.left_at)
        self.assertEqual(row.unread_count, 0)

    def test_user_covered_by_second_group_survives_leaving_first(self):
        self.conv.groups.add(self.team_b)
        self.bob.groups.add(self.team_b)
        self.bob.groups.remove(self.team_a)
        resync_conversation_members(self.conv)
        self.assertIn(self.bob.id, self._active_ids())

    def test_resync_is_idempotent(self):
        resync_conversation_members(self.conv)
        resync_conversation_members(self.conv)
        self.assertEqual(
            ConversationMember.objects.filter(conversation=self.conv).count(), 2
        )

    def test_bot_in_group_is_not_auto_joined(self):
        bot = User.objects.create_user(username="group-bot", password="pass123")
        BotProfile.objects.create(user=bot, is_public=True)
        bot.groups.add(self.team_a)
        resync_conversation_members(self.conv)
        self.assertNotIn(bot.id, self._active_ids())
        self.assertFalse(
            ConversationMember.objects.filter(conversation=self.conv, user=bot).exists()
        )


class GroupChangeSignalTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@test.com", password="pass123"
        )
        self.team_a = Group.objects.create(name="Team A")
        self.alice.groups.add(self.team_a)
        self.conv = create_group_conversation(self.alice, [self.team_a], title="Chan")

    def _active_ids(self):
        return set(
            ConversationMember.objects.filter(
                conversation=self.conv, left_at__isnull=True
            ).values_list("user_id", flat=True)
        )

    def test_user_added_to_group_gains_membership(self):
        self.bob.groups.add(self.team_a)
        self.assertIn(self.bob.id, self._active_ids())

    def test_reverse_direction_add(self):
        self.team_a.user_set.add(self.bob)
        self.assertIn(self.bob.id, self._active_ids())

    def test_user_removed_from_group_loses_membership(self):
        self.bob.groups.add(self.team_a)
        self.bob.groups.remove(self.team_a)
        self.assertNotIn(self.bob.id, self._active_ids())

    def test_user_groups_clear_loses_membership(self):
        self.bob.groups.add(self.team_a)
        self.bob.groups.clear()
        self.assertNotIn(self.bob.id, self._active_ids())

    def test_reverse_direction_remove(self):
        self.team_a.user_set.add(self.bob)
        self.team_a.user_set.remove(self.bob)
        self.assertNotIn(self.bob.id, self._active_ids())

    def test_uncovered_user_cannot_read_conversation(self):
        from workspace.chat.services.conversations import get_active_membership

        self.bob.groups.add(self.team_a)
        self.bob.groups.remove(self.team_a)
        self.assertIsNone(get_active_membership(self.bob, self.conv.uuid))

    def test_classic_conversation_unaffected_by_group_changes(self):
        classic = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Classic", created_by=self.alice
        )
        ConversationMember.objects.create(conversation=classic, user=self.bob)
        self.bob.groups.add(self.team_a)
        self.bob.groups.clear()
        row = ConversationMember.objects.get(conversation=classic, user=self.bob)
        self.assertIsNone(row.left_at)


class GroupDeletionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@test.com", password="pass123"
        )
        self.team_a = Group.objects.create(name="Team A")
        self.team_b = Group.objects.create(name="Team B")
        self.alice.groups.add(self.team_a)
        self.bob.groups.add(self.team_b)

    def test_deleting_only_group_deletes_conversation(self):
        conv = create_group_conversation(self.alice, [self.team_a], title="Chan")
        self.team_a.delete()
        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())

    def test_deleting_one_of_two_groups_detaches_and_resyncs(self):
        conv = create_group_conversation(
            self.alice, [self.team_a, self.team_b], title="Chan"
        )
        self.team_b.delete()
        conv.refresh_from_db()
        self.assertEqual(list(conv.groups.all()), [self.team_a])
        active = set(
            ConversationMember.objects.filter(
                conversation=conv, left_at__isnull=True
            ).values_list("user_id", flat=True)
        )
        self.assertEqual(active, {self.alice.id})

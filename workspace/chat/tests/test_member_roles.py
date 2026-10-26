import importlib
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.conversations import get_conversation_role
from workspace.chat.services.group_sync import create_group_conversation

from .test_chat import ChatTestMixin

User = get_user_model()

ADMIN = ConversationMember.Role.ADMIN
MEMBER = ConversationMember.Role.MEMBER


def _role(conversation, user):
    return ConversationMember.objects.get(conversation=conversation, user=user).role


class GetConversationRoleTests(ChatTestMixin, TestCase):
    def test_creator_is_admin(self):
        self.assertEqual(get_conversation_role(self.creator, self.group), ADMIN)

    def test_member_is_member(self):
        self.assertEqual(get_conversation_role(self.member, self.group.pk), MEMBER)

    def test_outsider_has_no_role(self):
        self.assertIsNone(get_conversation_role(self.outsider, self.group))

    def test_a_member_who_left_has_no_role(self):
        ConversationMember.objects.filter(
            conversation=self.group, user=self.creator
        ).update(left_at=timezone.now())
        self.assertIsNone(get_conversation_role(self.creator, self.group))


class CreateGroupRoleTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.carol = User.objects.create_user(username="carol", password="x")

    def test_creator_is_the_first_admin(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.post(
            "/api/v1/chat/conversations",
            {"member_ids": [self.bob.id, self.carol.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["my_role"], ADMIN)
        roles = {m["user"]["username"]: m["role"] for m in resp.data["members"]}
        self.assertEqual(roles, {"alice": ADMIN, "bob": MEMBER, "carol": MEMBER})

    def test_group_linked_creator_is_the_first_admin(self):
        team = Group.objects.create(name="Team")
        for user in (self.alice, self.bob, self.carol):
            user.groups.add(team)
        conversation = create_group_conversation(self.alice, [team])
        self.assertEqual(_role(conversation, self.alice), ADMIN)
        self.assertEqual(_role(conversation, self.bob), MEMBER)
        self.assertEqual(_role(conversation, self.carol), MEMBER)

    def test_both_people_of_a_dm_are_members(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.post(
            "/api/v1/chat/conversations", {"member_ids": [self.bob.id]}, format="json"
        )
        self.assertEqual(resp.data["my_role"], MEMBER)
        self.assertEqual({m["role"] for m in resp.data["members"]}, {MEMBER})


class ConversationReaderRoleTests(ChatTestMixin, APITestCase):
    def test_list_and_detail_report_the_reader_role(self):
        self.client.force_authenticate(self.member)
        listed = self.client.get("/api/v1/chat/conversations")
        group = next(c for c in listed.data if c["uuid"] == str(self.group.uuid))
        self.assertEqual(group["my_role"], MEMBER)
        roles = {m["user"]["username"]: m["role"] for m in group["members"]}
        self.assertEqual(roles, {"creator": ADMIN, "member": MEMBER})

        detail = self.client.get(f"/api/v1/chat/conversations/{self.group.uuid}")
        self.assertEqual(detail.data["my_role"], MEMBER)


class MemberRoleViewTests(ChatTestMixin, APITestCase):
    """PATCH /api/v1/chat/conversations/<id>/members/<user_id>"""

    def url(self, conversation, user):
        return f"/api/v1/chat/conversations/{conversation.uuid}/members/{user.id}"

    def _patch(self, actor, target, role, conversation=None):
        self.client.force_authenticate(actor)
        return self.client.patch(
            self.url(conversation or self.group, target), {"role": role}, format="json"
        )

    def test_admin_promotes_a_member(self):
        resp = self._patch(self.creator, self.member, ADMIN)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(_role(self.group, self.member), ADMIN)
        roles = {m["user"]["username"]: m["role"] for m in resp.data["members"]}
        self.assertEqual(roles["member"], ADMIN)

    def test_a_promoted_member_can_manage_the_group(self):
        self._patch(self.creator, self.member, ADMIN)
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            f"/api/v1/chat/conversations/{self.group.uuid}/members",
            {"user_ids": [self.extra_user.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_member_cannot_promote(self):
        resp = self._patch(self.member, self.member, ADMIN)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(_role(self.group, self.member), MEMBER)

    def test_outsider_cannot_change_roles(self):
        resp = self._patch(self.outsider, self.member, ADMIN)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_demotes_another_admin(self):
        self._patch(self.creator, self.member, ADMIN)
        resp = self._patch(self.member, self.creator, MEMBER)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(_role(self.group, self.creator), MEMBER)

    def test_admin_steps_down_when_another_admin_remains(self):
        self._patch(self.creator, self.member, ADMIN)
        resp = self._patch(self.creator, self.creator, MEMBER)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["my_role"], MEMBER)

    def test_last_admin_cannot_step_down(self):
        resp = self._patch(self.creator, self.creator, MEMBER)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(_role(self.group, self.creator), ADMIN)

    def test_unknown_role_rejected(self):
        resp = self._patch(self.creator, self.member, "owner")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_target_must_be_an_active_member(self):
        resp = self._patch(self.creator, self.outsider, ADMIN)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_dm_has_no_admins(self):
        resp = self._patch(self.creator, self.member, ADMIN, conversation=self.dm)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admins_of_a_group_linked_conversation_change_roles(self):
        team = Group.objects.create(name="Team")
        self.creator.groups.add(team)
        self.member.groups.add(team)
        linked = create_group_conversation(self.creator, [team])
        resp = self._patch(self.creator, self.member, ADMIN, conversation=linked)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(_role(linked, self.member), ADMIN)


class LastAdminHandOverTests(ChatTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        # The extra user joins after the member, so the member is the oldest.
        extra = ConversationMember.objects.create(
            conversation=self.group, user=self.extra_user
        )
        ConversationMember.objects.filter(pk=extra.pk).update(
            joined_at=timezone.now() + timedelta(minutes=1)
        )

    def _leave(self, user):
        self.client.force_authenticate(user)
        return self.client.delete(f"/api/v1/chat/conversations/{self.group.uuid}")

    def test_last_admin_leaving_promotes_the_oldest_member(self):
        resp = self._leave(self.creator)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(_role(self.group, self.member), ADMIN)
        self.assertEqual(_role(self.group, self.extra_user), MEMBER)

    def test_the_new_admin_can_remove_members(self):
        """Before roles, nobody could remove a member once the creator left."""
        self._leave(self.creator)
        self.client.force_authenticate(self.member)
        resp = self.client.delete(
            f"/api/v1/chat/conversations/{self.group.uuid}/members/{self.extra_user.id}"
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_leaving_admin_drops_the_role(self):
        self._leave(self.creator)
        self.assertEqual(_role(self.group, self.creator), MEMBER)

    def test_an_admin_leaving_with_another_admin_promotes_nobody(self):
        ConversationMember.objects.filter(
            conversation=self.group, user=self.extra_user
        ).update(role=ADMIN)
        self._leave(self.creator)
        self.assertEqual(_role(self.group, self.member), MEMBER)
        self.assertEqual(_role(self.group, self.extra_user), ADMIN)

    def test_a_removed_admin_comes_back_as_a_member(self):
        ConversationMember.objects.filter(
            conversation=self.group, user=self.member
        ).update(role=ADMIN)
        self.client.force_authenticate(self.creator)
        self.client.delete(
            f"/api/v1/chat/conversations/{self.group.uuid}/members/{self.member.id}"
        )
        self.assertEqual(_role(self.group, self.member), MEMBER)
        self.client.post(
            f"/api/v1/chat/conversations/{self.group.uuid}/members",
            {"user_ids": [self.member.id]},
            format="json",
        )
        self.assertEqual(get_conversation_role(self.member, self.group), MEMBER)


class GroupSyncAdminTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.conversation = create_group_conversation(self.alice, [self.team])

    def test_admin_leaving_the_linked_group_hands_the_conversation_over(self):
        self.alice.groups.remove(self.team)
        self.assertEqual(_role(self.conversation, self.alice), MEMBER)
        self.assertEqual(_role(self.conversation, self.bob), ADMIN)

    def test_admin_who_rejoins_the_linked_group_comes_back_as_a_member(self):
        self.alice.groups.remove(self.team)
        self.alice.groups.add(self.team)
        self.assertEqual(get_conversation_role(self.alice, self.conversation), MEMBER)

    def test_a_member_rejoining_an_emptied_linked_conversation_becomes_admin(self):
        """Everyone left, so nobody was promoted; whoever comes back first must be."""
        self.alice.groups.remove(self.team)
        self.bob.groups.remove(self.team)
        self.bob.groups.add(self.team)
        self.assertEqual(get_conversation_role(self.bob, self.conversation), ADMIN)


migration = importlib.import_module(
    "workspace.chat.migrations.0032_conversationmember_role"
)


class ConversationMemberRoleMigrationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.carol = User.objects.create_user(username="carol", password="x")

    def _group(self, creator, members):
        group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Team", created_by=creator
        )
        for offset, user in enumerate(members):
            row = ConversationMember.objects.create(conversation=group, user=user)
            ConversationMember.objects.filter(pk=row.pk).update(
                joined_at=timezone.now() + timedelta(minutes=offset)
            )
        return group

    def _forwards(self):
        migration.forwards(apps, SimpleNamespace(connection=connection))

    def test_the_creator_becomes_admin(self):
        group = self._group(self.alice, [self.alice, self.bob])
        self._forwards()
        self.assertEqual(_role(group, self.alice), ADMIN)
        self.assertEqual(_role(group, self.bob), MEMBER)

    def test_a_group_whose_creator_left_gets_its_oldest_member_as_admin(self):
        group = self._group(self.alice, [self.alice, self.bob, self.carol])
        ConversationMember.objects.filter(conversation=group, user=self.alice).update(
            left_at=timezone.now()
        )
        self._forwards()
        self.assertEqual(_role(group, self.alice), MEMBER)
        self.assertEqual(_role(group, self.bob), ADMIN)
        self.assertEqual(_role(group, self.carol), MEMBER)

    def test_direct_messages_get_no_admin(self):
        dm = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.alice
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=dm, user=user)
        self._forwards()
        self.assertEqual(_role(dm, self.alice), MEMBER)
        self.assertEqual(_role(dm, self.bob), MEMBER)

    def test_a_group_everyone_left_gets_no_admin(self):
        group = self._group(self.alice, [self.alice, self.bob])
        ConversationMember.objects.filter(conversation=group).update(
            left_at=timezone.now()
        )
        self._forwards()
        self.assertFalse(
            ConversationMember.objects.filter(conversation=group, role=ADMIN).exists()
        )


class StaleRowTests(ChatTestMixin, APITestCase):
    def test_set_member_role_does_not_promote_a_member_who_left_meanwhile(self):
        from workspace.chat.services.conversations import set_member_role

        stale = ConversationMember.objects.get(
            conversation=self.group, user=self.member
        )
        ConversationMember.objects.filter(pk=stale.pk).update(left_at=timezone.now())
        set_member_role(stale, ADMIN)
        self.assertEqual(_role(self.group, self.member), MEMBER)

    def test_re_adding_a_left_row_that_still_says_admin_resets_it(self):
        ConversationMember.objects.create(
            conversation=self.group,
            user=self.extra_user,
            role=ADMIN,
            left_at=timezone.now(),
        )
        self.client.force_authenticate(self.creator)
        self.client.post(
            f"/api/v1/chat/conversations/{self.group.uuid}/members",
            {"user_ids": [self.extra_user.id]},
            format="json",
        )
        self.assertEqual(get_conversation_role(self.extra_user, self.group), MEMBER)

    def test_group_sync_re_adding_a_left_row_that_still_says_admin_resets_it(self):
        team = Group.objects.create(name="Team")
        self.creator.groups.add(team)
        linked = create_group_conversation(self.creator, [team])
        ConversationMember.objects.create(
            conversation=linked, user=self.member, role=ADMIN, left_at=timezone.now()
        )
        self.member.groups.add(team)
        self.assertEqual(get_conversation_role(self.member, linked), MEMBER)

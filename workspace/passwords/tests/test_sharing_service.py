"""Unit tests for SharingService.

Covers every public method in workspace.passwords.services.sharing:
  - can_manage
  - can_write
  - invite_member
  - accept_invitation
  - revoke_member
  - update_member_role
  - share_with_group
  - list_members
  - get_pending_invitations
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.passwords.models import UserKeyPair, Vault, VaultGroupAccess, VaultMember
from workspace.passwords.services.sharing import SharingService
from workspace.passwords.services.vault import VaultService

User = get_user_model()

_SALT = 'A' * 43


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SharingServiceMixin:

    def setUp(self):
        self.alice = User.objects.create_user(username='alice_sh', email='alice_sh@test.com', password='pass')
        self.bob = User.objects.create_user(username='bob_sh', email='bob_sh@test.com', password='pass')
        self.charlie = User.objects.create_user(username='charlie_sh', email='charlie_sh@test.com', password='pass')
        self.vault = VaultService.create_vault(self.alice, name='Alice vault')
        self._give_keypair(self.bob)
        self._give_keypair(self.charlie)

    def _give_keypair(self, user):
        UserKeyPair.objects.create(
            user=user,
            public_key='pubkey-' + user.username,
            protected_private_key='enc-privkey',
            kdf_salt=_SALT,
        )

    def _invite(self, user=None, role=VaultMember.Role.VIEWER, vault=None, invited_by=None):
        return SharingService.invite_member(
            vault=vault or self.vault,
            invited_by=invited_by or self.alice,
            user=user or self.bob,
            role=role,
            protected_vault_key='enc-key',
        )

    def _accept(self, member: VaultMember) -> VaultMember:
        return SharingService.accept_invitation(member)

    def _member_with_role(self, user, role, status=VaultMember.Status.ACCEPTED) -> VaultMember:
        return VaultMember.objects.create(
            vault=self.vault, user=user, role=role,
            status=status, protected_vault_key='enc-key',
        )


# ---------------------------------------------------------------------------
# can_manage
# ---------------------------------------------------------------------------

class CanManageTests(SharingServiceMixin, TestCase):

    def test_owner_can_manage(self):
        self.assertTrue(SharingService.can_manage(self.vault, self.alice))

    def test_accepted_manager_can_manage(self):
        self._member_with_role(self.bob, VaultMember.Role.MANAGER)
        self.assertTrue(SharingService.can_manage(self.vault, self.bob))

    def test_accepted_editor_cannot_manage(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR)
        self.assertFalse(SharingService.can_manage(self.vault, self.bob))

    def test_accepted_viewer_cannot_manage(self):
        self._member_with_role(self.bob, VaultMember.Role.VIEWER)
        self.assertFalse(SharingService.can_manage(self.vault, self.bob))

    def test_pending_manager_cannot_manage(self):
        self._member_with_role(self.bob, VaultMember.Role.MANAGER, status=VaultMember.Status.PENDING)
        self.assertFalse(SharingService.can_manage(self.vault, self.bob))

    def test_non_member_cannot_manage(self):
        self.assertFalse(SharingService.can_manage(self.vault, self.charlie))


# ---------------------------------------------------------------------------
# can_write
# ---------------------------------------------------------------------------

class CanWriteTests(SharingServiceMixin, TestCase):

    def test_owner_can_write(self):
        self.assertTrue(SharingService.can_write(self.vault, self.alice))

    def test_accepted_manager_can_write(self):
        self._member_with_role(self.bob, VaultMember.Role.MANAGER)
        self.assertTrue(SharingService.can_write(self.vault, self.bob))

    def test_accepted_editor_can_write(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR)
        self.assertTrue(SharingService.can_write(self.vault, self.bob))

    def test_accepted_viewer_cannot_write(self):
        self._member_with_role(self.bob, VaultMember.Role.VIEWER)
        self.assertFalse(SharingService.can_write(self.vault, self.bob))

    def test_pending_editor_cannot_write(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR, status=VaultMember.Status.PENDING)
        self.assertFalse(SharingService.can_write(self.vault, self.bob))

    def test_non_member_cannot_write(self):
        self.assertFalse(SharingService.can_write(self.vault, self.charlie))


# ---------------------------------------------------------------------------
# invite_member
# ---------------------------------------------------------------------------

class InviteMemberTests(SharingServiceMixin, TestCase):

    def test_owner_can_invite(self):
        member = self._invite()
        self.assertIsInstance(member, VaultMember)

    def test_accepted_manager_can_invite(self):
        self._member_with_role(self.bob, VaultMember.Role.MANAGER)
        member = self._invite(user=self.charlie, invited_by=self.bob)
        self.assertIsInstance(member, VaultMember)

    def test_editor_cannot_invite(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR)
        with self.assertRaises(PermissionError):
            self._invite(user=self.charlie, invited_by=self.bob)

    def test_viewer_cannot_invite(self):
        self._member_with_role(self.bob, VaultMember.Role.VIEWER)
        with self.assertRaises(PermissionError):
            self._invite(user=self.charlie, invited_by=self.bob)

    def test_non_member_cannot_invite(self):
        with self.assertRaises(PermissionError):
            self._invite(user=self.charlie, invited_by=self.bob)

    def test_cannot_invite_vault_owner(self):
        with self.assertRaises(ValueError):
            self._invite(user=self.alice)

    def test_cannot_invite_user_without_keypair(self):
        user_no_key = User.objects.create_user(username='nokey', email='nokey@test.com', password='pass')
        with self.assertRaises(ValueError):
            self._invite(user=user_no_key)

    def test_cannot_invite_user_with_pending_membership(self):
        self._invite()  # creates pending membership for bob
        with self.assertRaises(ValueError):
            self._invite()  # try to invite again

    def test_cannot_invite_user_with_accepted_membership(self):
        member = self._invite()
        self._accept(member)
        with self.assertRaises(ValueError):
            self._invite()

    def test_can_reinvite_user_with_revoked_membership(self):
        member = self._invite()
        member.status = VaultMember.Status.REVOKED
        member.save()
        new_member = self._invite()
        self.assertEqual(new_member.status, VaultMember.Status.PENDING)

    def test_creates_member_with_correct_role(self):
        member = self._invite(role=VaultMember.Role.EDITOR)
        self.assertEqual(member.role, VaultMember.Role.EDITOR)

    def test_creates_member_with_pending_status(self):
        member = self._invite()
        self.assertEqual(member.status, VaultMember.Status.PENDING)

    def test_stores_protected_vault_key(self):
        member = SharingService.invite_member(
            vault=self.vault, invited_by=self.alice, user=self.bob,
            role=VaultMember.Role.VIEWER, protected_vault_key='my-enc-key',
        )
        self.assertEqual(member.protected_vault_key, 'my-enc-key')

    def test_sets_invited_by(self):
        member = self._invite()
        self.assertEqual(member.invited_by, self.alice)

    def test_is_persisted(self):
        member = self._invite()
        self.assertTrue(VaultMember.objects.filter(pk=member.pk).exists())


# ---------------------------------------------------------------------------
# accept_invitation
# ---------------------------------------------------------------------------

class AcceptInvitationTests(SharingServiceMixin, TestCase):

    def test_sets_status_to_accepted(self):
        member = self._invite()
        self._accept(member)
        member.refresh_from_db()
        self.assertEqual(member.status, VaultMember.Status.ACCEPTED)

    def test_persists_accepted_status(self):
        member = self._invite()
        self._accept(member)
        from_db = VaultMember.objects.get(pk=member.pk)
        self.assertEqual(from_db.status, VaultMember.Status.ACCEPTED)

    def test_cannot_accept_already_accepted_invitation(self):
        member = self._invite()
        self._accept(member)
        with self.assertRaises(ValueError):
            self._accept(member)

    def test_cannot_accept_revoked_invitation(self):
        member = self._invite()
        member.status = VaultMember.Status.REVOKED
        member.save()
        with self.assertRaises(ValueError):
            self._accept(member)


# ---------------------------------------------------------------------------
# revoke_member
# ---------------------------------------------------------------------------

class RevokeMemberTests(SharingServiceMixin, TestCase):

    def test_owner_can_revoke_member(self):
        self._invite()
        SharingService.revoke_member(self.vault, self.bob, revoked_by=self.alice)
        self.assertEqual(
            VaultMember.objects.get(vault=self.vault, user=self.bob).status,
            VaultMember.Status.REVOKED,
        )

    def test_manager_can_revoke_member(self):
        self._member_with_role(self.bob, VaultMember.Role.MANAGER)
        self._invite(user=self.charlie)
        SharingService.revoke_member(self.vault, self.charlie, revoked_by=self.bob)
        self.assertEqual(
            VaultMember.objects.get(vault=self.vault, user=self.charlie).status,
            VaultMember.Status.REVOKED,
        )

    def test_editor_cannot_revoke(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR)
        self._invite(user=self.charlie)
        with self.assertRaises(PermissionError):
            SharingService.revoke_member(self.vault, self.charlie, revoked_by=self.bob)

    def test_revoke_sets_status_to_revoked(self):
        member = self._invite()
        self._accept(member)
        SharingService.revoke_member(self.vault, self.bob, revoked_by=self.alice)
        member.refresh_from_db()
        self.assertEqual(member.status, VaultMember.Status.REVOKED)

    def test_revoke_pending_member(self):
        self._invite()
        SharingService.revoke_member(self.vault, self.bob, revoked_by=self.alice)
        self.assertEqual(
            VaultMember.objects.get(vault=self.vault, user=self.bob).status,
            VaultMember.Status.REVOKED,
        )


# ---------------------------------------------------------------------------
# update_member_role
# ---------------------------------------------------------------------------

class UpdateMemberRoleTests(SharingServiceMixin, TestCase):

    def test_owner_can_update_role(self):
        member = self._invite(role=VaultMember.Role.VIEWER)
        SharingService.update_member_role(member, VaultMember.Role.EDITOR, updated_by=self.alice)
        member.refresh_from_db()
        self.assertEqual(member.role, VaultMember.Role.EDITOR)

    def test_manager_can_update_role(self):
        self._member_with_role(self.bob, VaultMember.Role.MANAGER)
        member = self._invite(user=self.charlie, role=VaultMember.Role.VIEWER)
        SharingService.update_member_role(member, VaultMember.Role.EDITOR, updated_by=self.bob)
        member.refresh_from_db()
        self.assertEqual(member.role, VaultMember.Role.EDITOR)

    def test_editor_cannot_update_role(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR)
        member = self._invite(user=self.charlie, role=VaultMember.Role.VIEWER)
        with self.assertRaises(PermissionError):
            SharingService.update_member_role(member, VaultMember.Role.EDITOR, updated_by=self.bob)

    def test_role_change_is_persisted(self):
        member = self._invite(role=VaultMember.Role.VIEWER)
        SharingService.update_member_role(member, VaultMember.Role.MANAGER, updated_by=self.alice)
        from_db = VaultMember.objects.get(pk=member.pk)
        self.assertEqual(from_db.role, VaultMember.Role.MANAGER)


# ---------------------------------------------------------------------------
# list_members
# ---------------------------------------------------------------------------

class ListMembersTests(SharingServiceMixin, TestCase):

    def test_returns_empty_when_no_members(self):
        self.assertEqual(SharingService.list_members(self.vault).count(), 0)

    def test_returns_pending_members(self):
        self._invite()
        self.assertEqual(SharingService.list_members(self.vault).count(), 1)

    def test_returns_accepted_members(self):
        member = self._invite()
        self._accept(member)
        self.assertEqual(SharingService.list_members(self.vault).count(), 1)

    def test_excludes_revoked_members(self):
        member = self._invite()
        member.status = VaultMember.Status.REVOKED
        member.save()
        self.assertEqual(SharingService.list_members(self.vault).count(), 0)

    def test_returns_multiple_members(self):
        self._invite(user=self.bob)
        self._invite(user=self.charlie)
        self.assertEqual(SharingService.list_members(self.vault).count(), 2)

    def test_is_scoped_to_vault(self):
        other_vault = VaultService.create_vault(self.alice, name='Other')
        SharingService.invite_member(
            vault=other_vault, invited_by=self.alice, user=self.bob,
            role=VaultMember.Role.VIEWER, protected_vault_key='k',
        )
        self.assertEqual(SharingService.list_members(self.vault).count(), 0)


# ---------------------------------------------------------------------------
# get_pending_invitations
# ---------------------------------------------------------------------------

class GetPendingInvitationsTests(SharingServiceMixin, TestCase):

    def test_returns_pending_invitation(self):
        self._invite()
        self.assertEqual(SharingService.get_pending_invitations(self.bob).count(), 1)

    def test_excludes_accepted_invitation(self):
        member = self._invite()
        self._accept(member)
        self.assertEqual(SharingService.get_pending_invitations(self.bob).count(), 0)

    def test_excludes_revoked_invitation(self):
        member = self._invite()
        member.status = VaultMember.Status.REVOKED
        member.save()
        self.assertEqual(SharingService.get_pending_invitations(self.bob).count(), 0)

    def test_returns_empty_when_no_invitations(self):
        self.assertEqual(SharingService.get_pending_invitations(self.bob).count(), 0)

    def test_scoped_to_user(self):
        self._invite(user=self.bob)
        self.assertEqual(SharingService.get_pending_invitations(self.charlie).count(), 0)

    def test_returns_multiple_pending_invitations(self):
        vault2 = VaultService.create_vault(self.alice, name='Work')
        SharingService.invite_member(
            vault=vault2, invited_by=self.alice, user=self.bob,
            role=VaultMember.Role.VIEWER, protected_vault_key='k',
        )
        self._invite(user=self.bob)
        self.assertEqual(SharingService.get_pending_invitations(self.bob).count(), 2)


# ---------------------------------------------------------------------------
# share_with_group
# ---------------------------------------------------------------------------

class ShareWithGroupTests(SharingServiceMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.group = Group.objects.create(name='Team')

    def test_creates_vault_group_access(self):
        SharingService.share_with_group(
            vault=self.vault, group=self.group, role=VaultMember.Role.VIEWER,
            granted_by=self.alice, members_data=[],
        )
        self.assertTrue(VaultGroupAccess.objects.filter(vault=self.vault, group=self.group).exists())

    def test_stores_role_on_group_access(self):
        SharingService.share_with_group(
            vault=self.vault, group=self.group, role=VaultMember.Role.EDITOR,
            granted_by=self.alice, members_data=[],
        )
        access = VaultGroupAccess.objects.get(vault=self.vault, group=self.group)
        self.assertEqual(access.role, VaultMember.Role.EDITOR)

    def test_creates_vault_member_for_each_member_data(self):
        members_data = [
            {'user': self.bob, 'protected_vault_key': 'enc-key-bob'},
            {'user': self.charlie, 'protected_vault_key': 'enc-key-charlie'},
        ]
        created = SharingService.share_with_group(
            vault=self.vault, group=self.group, role=VaultMember.Role.VIEWER,
            granted_by=self.alice, members_data=members_data,
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(VaultMember.objects.filter(vault=self.vault).count(), 2)

    def test_idempotent_group_access(self):
        SharingService.share_with_group(
            vault=self.vault, group=self.group, role=VaultMember.Role.VIEWER,
            granted_by=self.alice, members_data=[],
        )
        SharingService.share_with_group(
            vault=self.vault, group=self.group, role=VaultMember.Role.EDITOR,
            granted_by=self.alice, members_data=[],
        )
        self.assertEqual(VaultGroupAccess.objects.filter(vault=self.vault, group=self.group).count(), 1)
        access = VaultGroupAccess.objects.get(vault=self.vault, group=self.group)
        self.assertEqual(access.role, VaultMember.Role.EDITOR)

    def test_non_manager_cannot_share_with_group(self):
        self._member_with_role(self.bob, VaultMember.Role.EDITOR)
        with self.assertRaises(PermissionError):
            SharingService.share_with_group(
                vault=self.vault, group=self.group, role=VaultMember.Role.VIEWER,
                granted_by=self.bob, members_data=[],
            )

    def test_sets_granted_by(self):
        SharingService.share_with_group(
            vault=self.vault, group=self.group, role=VaultMember.Role.VIEWER,
            granted_by=self.alice, members_data=[],
        )
        access = VaultGroupAccess.objects.get(vault=self.vault, group=self.group)
        self.assertEqual(access.granted_by, self.alice)

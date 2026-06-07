"""SharingService — zero-knowledge vault sharing and permission management."""

from django.contrib.auth.models import Group
from django.db.models import QuerySet

from ..models import Vault, VaultGroupAccess, VaultMember


class SharingService:
    """Stateless service for vault sharing and member management.

    Permission hierarchy (highest to lowest):
      owner (vault.user) > manager > editor > viewer

    Only the owner and managers may invite or revoke members.
    """

    # ------------------------------------------------------------------
    # Permission helpers
    # ------------------------------------------------------------------

    @staticmethod
    def can_manage(vault: Vault, user) -> bool:
        """Return True if *user* may invite/revoke members on *vault*."""
        if vault.user == user:
            return True
        return VaultMember.objects.filter(
            vault=vault,
            user=user,
            role=VaultMember.Role.MANAGER,
            status=VaultMember.Status.ACCEPTED,
        ).exists()

    @staticmethod
    def can_write(vault: Vault, user) -> bool:
        """Return True if *user* may create/update/delete entries in *vault*."""
        if vault.user == user:
            return True
        return VaultMember.objects.filter(
            vault=vault,
            user=user,
            role__in=[VaultMember.Role.EDITOR, VaultMember.Role.MANAGER],
            status=VaultMember.Status.ACCEPTED,
        ).exists()

    # ------------------------------------------------------------------
    # Individual sharing
    # ------------------------------------------------------------------

    @staticmethod
    def invite_member(vault: Vault, invited_by, user, role: str,
                      protected_vault_key: str) -> VaultMember:
        """Invite *user* to *vault* with the given *role*.

        ``protected_vault_key`` is the vault key re-encrypted client-side with
        the invitee's ECDH public key.  The server stores it opaque.

        Raises ``PermissionError`` if *invited_by* cannot manage the vault.
        Raises ``ValueError`` if *user* already has an active membership or has
        no registered keypair.
        """
        if not SharingService.can_manage(vault, invited_by):
            raise PermissionError('Only the vault owner or a manager can invite members.')

        if vault.user == user:
            raise ValueError('The vault owner cannot be added as a member.')

        from .keypair import KeyPairService
        if not KeyPairService.get_public_key(user):
            raise ValueError('The invited user has not set up a keypair.')

        existing = VaultMember.objects.filter(vault=vault, user=user).first()
        if existing and existing.status != VaultMember.Status.REVOKED:
            raise ValueError('User already has an active or pending membership.')

        if existing:
            existing.role = role
            existing.status = VaultMember.Status.PENDING
            existing.protected_vault_key = protected_vault_key
            existing.invited_by = invited_by
            existing.save()
            return existing

        return VaultMember.objects.create(
            vault=vault,
            user=user,
            role=role,
            status=VaultMember.Status.PENDING,
            protected_vault_key=protected_vault_key,
            invited_by=invited_by,
        )

    @staticmethod
    def accept_invitation(member: VaultMember) -> VaultMember:
        """Accept a pending vault invitation."""
        if member.status != VaultMember.Status.PENDING:
            raise ValueError('Invitation is not in pending state.')
        member.status = VaultMember.Status.ACCEPTED
        member.save(update_fields=['status', 'updated_at'])
        return member

    @staticmethod
    def revoke_member(vault: Vault, user, revoked_by) -> None:
        """Revoke *user*'s access to *vault*.

        Raises ``PermissionError`` if *revoked_by* cannot manage the vault.
        """
        if not SharingService.can_manage(vault, revoked_by):
            raise PermissionError('Only the vault owner or a manager can revoke members.')
        VaultMember.objects.filter(vault=vault, user=user).update(
            status=VaultMember.Status.REVOKED
        )

    @staticmethod
    def update_member_role(member: VaultMember, new_role: str, updated_by) -> VaultMember:
        """Change the role of an existing member.

        Raises ``PermissionError`` if *updated_by* cannot manage the vault.
        """
        if not SharingService.can_manage(member.vault, updated_by):
            raise PermissionError('Only the vault owner or a manager can change roles.')
        member.role = new_role
        member.save(update_fields=['role', 'updated_at'])
        return member

    # ------------------------------------------------------------------
    # Group sharing
    # ------------------------------------------------------------------

    @staticmethod
    def share_with_group(vault: Vault, group: Group, role: str, granted_by,
                          members_data: list[dict]) -> list[VaultMember]:
        """Grant *group* access to *vault* and create individual VaultMember rows.

        ``members_data`` is a list of ``{'user': user, 'protected_vault_key': str}``
        dicts — one entry per current group member whose public key is known.
        The caller's client is responsible for encrypting the vault key for each.

        Creates (or updates) a ``VaultGroupAccess`` record, then calls
        ``invite_member`` for each entry in ``members_data``.
        """
        if not SharingService.can_manage(vault, granted_by):
            raise PermissionError('Only the vault owner or a manager can share with a group.')

        VaultGroupAccess.objects.update_or_create(
            vault=vault,
            group=group,
            defaults={'role': role, 'granted_by': granted_by},
        )

        created: list[VaultMember] = []
        for entry in members_data:
            member = SharingService.invite_member(
                vault=vault,
                invited_by=granted_by,
                user=entry['user'],
                role=role,
                protected_vault_key=entry['protected_vault_key'],
            )
            created.append(member)
        return created

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @staticmethod
    def list_members(vault: Vault) -> QuerySet:
        """Return active (non-revoked) members of *vault*."""
        return VaultMember.objects.filter(vault=vault).exclude(
            status=VaultMember.Status.REVOKED
        ).select_related('user', 'invited_by')

    @staticmethod
    def get_pending_invitations(user) -> QuerySet:
        """Return pending vault invitations for *user*."""
        return VaultMember.objects.filter(
            user=user, status=VaultMember.Status.PENDING
        ).select_related('vault', 'invited_by')

"""VaultService — centralised access control and vault lifecycle for the passwords module.

All password-vault business logic that doesn't belong in a view or serializer
lives here.  Call sites include views, REST endpoints, and future Celery tasks.

Access control contract
───────────────────────
Never write raw ORM filters to check ownership.  Always go through
``VaultService.accessible_entries_q`` so permission logic stays in one place.
"""

import base64
import os

from django.contrib.auth.hashers import check_password, make_password
from django.db.models import Q, QuerySet

from ..models import LoginEntry, Vault, VaultMember


class VaultService:
    """Stateless service for vault lifecycle and entry access control."""

    # ------------------------------------------------------------------
    # KDF salt
    # ------------------------------------------------------------------

    @staticmethod
    def generate_kdf_salt(user) -> str:
        """Return a server-generated composite KDF salt.

        Format: base64url( sha256(str(user.pk))[:16] || os.urandom(16) )

        Binding the salt to the user identity ensures that even if two users
        share the same master password their derived vault keys differ.
        """
        import hashlib
        user_bytes = hashlib.sha256(str(user.pk).encode()).digest()[:16]
        random_bytes = os.urandom(16)
        return base64.urlsafe_b64encode(user_bytes + random_bytes).rstrip(b'=').decode()

    # ------------------------------------------------------------------
    # Vault lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def create_vault(user, name: str = 'Personal', description: str = '',
                     icon: str = 'vault', color: str = 'primary') -> Vault:
        """Create and persist a new vault for *user*.

        Multiple vaults per user are allowed (Personal, Work, …).
        The vault is not set up until :meth:`setup_vault` is called.
        A composite KDF salt is pre-generated and stored on the vault so the
        client can use it immediately during setup.
        """
        return Vault.objects.create(
            user=user,
            name=name,
            description=description,
            icon=icon,
            color=color,
            kdf_salt=VaultService.generate_kdf_salt(user),
        )

    @staticmethod
    def list_vaults(user) -> QuerySet:
        """Return all vaults the user owns or has accepted membership in."""
        return Vault.objects.filter(
            Q(user=user) | Q(members__user=user, members__status=VaultMember.Status.ACCEPTED)
        ).distinct()

    @staticmethod
    def get_vault(user, vault_uuid) -> Vault | None:
        """Return the vault if *user* owns it or has accepted membership.

        Returns ``None`` when not found or access is denied.
        """
        return Vault.objects.filter(
            Q(user=user) | Q(members__user=user, members__status=VaultMember.Status.ACCEPTED),
            uuid=vault_uuid,
        ).first()

    @staticmethod
    def setup_vault(vault: Vault, client_master_hash: str, protected_vault_key: str,
                    kdf_salt: str | None = None, kdf_algorithm: str | None = None,
                    kdf_iterations: int | None = None,
                    kdf_memory: int | None = None,
                    kdf_parallelism: int | None = None) -> Vault:
        """Set or rotate the master password on a vault.

        The server hashes ``client_master_hash`` with Django's password hasher
        (PBKDF2-SHA256 by default) so the raw hash is never stored.

        ``protected_vault_key`` is the vault's AES key, itself encrypted
        client-side with the stretched master key — the server stores it opaque.

        ``kdf_salt`` is optional: when omitted the server-generated composite salt
        already stored on the vault is preserved.  Pass it explicitly only when
        rotating the master password and re-deriving the salt.
        """
        vault.master_password_hash = make_password(client_master_hash)
        vault.protected_vault_key = protected_vault_key
        if kdf_salt is not None:
            vault.kdf_salt = kdf_salt
        if kdf_algorithm is not None:
            vault.kdf_algorithm = kdf_algorithm
        if kdf_iterations is not None:
            vault.kdf_iterations = kdf_iterations
        if kdf_memory is not None:
            vault.kdf_memory = kdf_memory
        if kdf_parallelism is not None:
            vault.kdf_parallelism = kdf_parallelism
        vault.is_setup = True
        vault.save()
        return vault

    @staticmethod
    def verify_unlock(vault: Vault, client_master_hash: str) -> tuple[bool, str]:
        """Check ``client_master_hash`` against the stored hash.

        Returns ``(True, protected_vault_key)`` on success,
        ``(False, '')`` on failure or when the vault has not been set up yet.
        """
        if not vault.is_setup:
            return False, ''
        if check_password(client_master_hash, vault.master_password_hash):
            return True, vault.protected_vault_key
        return False, ''

    # ------------------------------------------------------------------
    # Access control — LoginEntry
    # ------------------------------------------------------------------

    @staticmethod
    def accessible_entries_q(user) -> Q:
        """Return a Q filter matching all LoginEntry rows the user may access.

        Includes entries from vaults the user owns AND vaults where the user
        has an accepted VaultMember record.
        """
        return Q(vault__user=user) | Q(
            vault__members__user=user,
            vault__members__status=VaultMember.Status.ACCEPTED,
        )

    @staticmethod
    def get_login_entries(user, vault_uuid=None, include_deleted: bool = False) -> QuerySet:
        """Return a queryset of LoginEntry rows accessible to *user*.

        ``vault_uuid``      — optional filter to a specific vault.
        ``include_deleted`` — when True, also return soft-deleted entries.
        """
        qs = LoginEntry.objects.filter(VaultService.accessible_entries_q(user)).distinct()
        if vault_uuid is not None:
            qs = qs.filter(vault_id=vault_uuid)
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        return qs

    @staticmethod
    def delete_vault(user, vault_uuid) -> bool:
        """Permanently delete a vault and all its contents.

        Only the vault owner may delete. Returns True on success, False when
        the vault does not exist or the user is not the owner.
        Django CASCADE removes all entries, folders, tags, members, and group
        accesses automatically.
        """
        vault = Vault.objects.filter(uuid=vault_uuid, user=user).first()
        if vault is None:
            return False
        vault.delete()
        return True

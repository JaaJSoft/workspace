"""KeyPairService — manage per-user ECDH keypairs for zero-knowledge vault sharing."""

from ..models import UserKeyPair
from .vault import VaultService


class KeyPairService:
    """Stateless service for UserKeyPair lifecycle."""

    @staticmethod
    def create_or_update_keypair(user, public_key: str, protected_private_key: str,
                                  kdf_salt: str | None = None) -> UserKeyPair:
        """Persist the user's asymmetric keypair.

        Called when the user sets up their first vault.  If a keypair already
        exists it is replaced (e.g. after a master-password rotation).

        ``kdf_salt`` is the composite salt used client-side to derive the key
        that protects the private key.  When omitted a fresh composite salt is
        generated server-side.
        """
        if kdf_salt is None:
            kdf_salt = VaultService.generate_kdf_salt(user)

        keypair, _ = UserKeyPair.objects.update_or_create(
            user=user,
            defaults={
                'public_key': public_key,
                'protected_private_key': protected_private_key,
                'kdf_salt': kdf_salt,
            },
        )
        return keypair

    @staticmethod
    def get_public_key(user) -> str | None:
        """Return the plaintext ECDH public key for *user*, or None if not set up."""
        try:
            return user.key_pair.public_key
        except UserKeyPair.DoesNotExist:
            return None

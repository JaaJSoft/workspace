"""Removing an account's cryptographic identity."""

from django.db import transaction

from ..models import Vault, VaultKeyWrap


def sealed_to(identity):
    """What deleting *identity* leaves nobody able to open: the key wraps of
    its owner, sealed to the key pair that goes with it, and the vaults of that
    owner no other member holds a key for.

    A vault somebody else can still open is not in it, whoever owns it.
    """
    user = identity.user
    wraps = VaultKeyWrap.objects.filter(recipient=user)
    held_by_others = VaultKeyWrap.objects.exclude(recipient=user).values("vault_id")
    vaults = Vault.objects.filter(owner=user).exclude(uuid__in=held_by_others)
    return wraps, vaults


def delete_identity(identity):
    """Delete *identity* together with what only it could open."""
    with transaction.atomic():
        wraps, vaults = sealed_to(identity)
        vaults.delete()
        wraps.delete()
        identity.delete()

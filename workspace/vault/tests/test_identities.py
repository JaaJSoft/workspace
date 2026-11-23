"""What goes with an account identity when it is deleted.

The identity is the key pair every key wrap of its owner is sealed to. Without
it those wraps open nothing, and a vault nobody else holds a key for is
ciphertext for good: leaving either behind lists its owner a vault that can
never open.
"""

from django.test import TestCase

from workspace.vault.models import AccountIdentity, Vault, VaultEntry
from workspace.vault.services.identities import delete_identity
from workspace.vault.tests.factories import (
    make_account,
    make_key_wrap,
    make_vault,
    sealed,
)

SIGNATURE = "AXNpZ25hdHVyZQ"


class DeleteIdentityTests(TestCase):
    def setUp(self):
        self.user, _signer, self.identity = make_account("leaving")
        self.other, _signer, self.other_identity = make_account("staying")

    def _vault(self, owner, *members):
        vault = make_vault(owner)
        for user in (owner, *members):
            make_key_wrap(vault, user)
        return vault

    def _key_holders(self, vault):
        return set(vault.key_wraps.values_list("recipient_id", flat=True))

    def test_a_vault_only_its_owner_could_open_goes_with_the_identity(self):
        vault = self._vault(self.user)
        VaultEntry.objects.create(
            vault=vault, encrypted_name=sealed("entry"), metadata_sig=SIGNATURE
        )

        delete_identity(self.identity)

        self.assertFalse(AccountIdentity.objects.filter(pk=self.identity.pk).exists())
        self.assertFalse(Vault.objects.filter(pk=vault.pk).exists())
        self.assertFalse(VaultEntry.objects.filter(vault_id=vault.pk).exists())

    def test_a_vault_another_member_can_open_stays(self):
        shared = self._vault(self.user, self.other)

        delete_identity(self.identity)

        self.assertEqual(Vault.objects.get(pk=shared.pk).owner, self.user)
        self.assertEqual(self._key_holders(shared), {self.other.pk})

    def test_the_owner_leaves_the_vaults_they_were_a_member_of(self):
        theirs = self._vault(self.other, self.user)

        delete_identity(self.identity)

        self.assertEqual(self._key_holders(theirs), {self.other.pk})

    def test_other_accounts_are_left_alone(self):
        theirs = self._vault(self.other)

        delete_identity(self.identity)

        self.assertTrue(
            AccountIdentity.objects.filter(pk=self.other_identity.pk).exists()
        )
        self.assertEqual(self._key_holders(theirs), {self.other.pk})

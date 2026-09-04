"""What is left of the vault after its owner's account is deleted.

Nothing, is the answer, and the reason is worth stating: every route from a
user into this module is a CASCADE foreign key, and the module owns no blob
storage and registers no delete signal. That makes the guarantee structural
rather than procedural - there is no purge routine to call and forget, and no
place for one to rot.

Two shapes are pinned because they are where a cascade fails quietly:

* an entry sitting in a folder. ``VaultEntry.folder`` is RESTRICT, so the
  database refuses to orphan it - and would refuse the whole deletion if the
  entry were not collected by the same operation. It is, through its vault,
  which is exactly the property this file exists to hold. Nothing else in the
  suite exercises the whole graph at once.
* a vault already in the trash. ``deleted_at`` is a column, not a state the
  cascade knows about, so a soft-deleted row is as real as any other.

One consequence is deliberate and not a defect: ``Vault.owner`` is CASCADE, so
deleting an owner destroys the vault for every member it was shared with.
Correct while a vault has a single user; a decision to take again, explicitly,
before sharing ships.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import (
    AccountIdentity,
    EntryField,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)
from workspace.vault.tests.test_models import SIGNATURE, make_identity

User = get_user_model()

# Every model the module owns. A new one has to be listed here, or this test
# keeps passing while its rows survive every account deletion.
VAULT_MODELS = (
    AccountIdentity,
    Vault,
    VaultKeyWrap,
    VaultFolder,
    VaultTag,
    VaultEntry,
    EntryField,
)


class AccountDeletionPurgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.bystander = User.objects.create_user(username="other", password="pw")

    def _populate(self, owner, *, trashed=False):
        """A vault with a folder tree, tags, entries inside folders and fields."""
        make_identity(owner)
        vault = Vault.objects.create(
            owner=owner, encrypted_name="ct", metadata_sig=SIGNATURE
        )
        VaultKeyWrap.objects.create(
            vault=vault, recipient=owner, wrapped_key="ct", hpke_suite={}
        )
        parent = VaultFolder.objects.create(
            vault=vault, encrypted_name="ct", metadata_sig=SIGNATURE
        )
        child = VaultFolder.objects.create(
            vault=vault, parent=parent, encrypted_name="ct", metadata_sig=SIGNATURE
        )
        # clean() is what rejects a cross-vault or cyclic parent, and nothing
        # calls it on its own - so the fixture is only a real tree if it runs.
        child.full_clean()
        tag = VaultTag.objects.create(
            vault=vault, encrypted_name="ct", metadata_sig=SIGNATURE
        )
        for folder in (parent, child, None):
            entry = VaultEntry.objects.create(
                vault=vault,
                folder=folder,
                encrypted_name="ct",
                metadata_sig=SIGNATURE,
                deleted_at=timezone.now() if trashed else None,
            )
            entry.tags.add(tag)
            EntryField.objects.create(
                entry=entry, field_id="password", encrypted_value="ct"
            )
        return vault

    def _counts(self):
        return {model.__name__: model.objects.count() for model in VAULT_MODELS}

    def test_nothing_of_the_module_survives_the_account(self):
        self._populate(self.user)
        self.assertTrue(all(self._counts().values()), "the fixture populated nothing")

        self.user.delete()

        self.assertEqual(self._counts(), {model.__name__: 0 for model in VAULT_MODELS})

    def test_a_bulk_delete_purges_the_same_way(self):
        """The path an account is actually deleted by today.

        No view deletes an account: it happens through the admin's bulk action,
        which calls ``delete()`` on a queryset rather than on an instance. Both
        go through the same collector, but only one of them is what runs in
        practice - and the whole guarantee here is structural, so it should not
        matter which. This says it does not.
        """
        self._populate(self.user)

        User.objects.filter(pk=self.user.pk).delete()

        self.assertEqual(self._counts(), {model.__name__: 0 for model in VAULT_MODELS})

    def test_a_trashed_vault_goes_too(self):
        """deleted_at is a column, not a state the cascade knows about."""
        self._populate(self.user, trashed=True)

        self.user.delete()

        self.assertEqual(VaultEntry.objects.count(), 0)
        self.assertEqual(Vault.objects.count(), 0)

    def test_the_entry_tag_links_go_with_the_entries(self):
        """The through table has no cascade of its own to look at."""
        vault = self._populate(self.user)
        through = VaultEntry.tags.through
        self.assertTrue(through.objects.filter(vaultentry__vault=vault).exists())

        self.user.delete()

        self.assertEqual(through.objects.count(), 0)

    def test_another_account_keeps_everything(self):
        """The blast radius is one account, which is the other half of the
        claim: a purge that took a neighbour's vault with it would satisfy
        every assertion above."""
        self._populate(self.user)
        self._populate(self.bystander)

        self.user.delete()

        self.assertEqual(Vault.objects.filter(owner=self.bystander).count(), 1)
        self.assertEqual(AccountIdentity.objects.count(), 1)
        self.assertEqual(VaultEntry.objects.count(), 3)

    def test_a_member_leaving_does_not_take_the_vault(self):
        """Deleting a recipient removes their wrap and nothing else - the
        mirror of the owner case, and the reason the owner case is worth
        writing down."""
        vault = self._populate(self.user)
        VaultKeyWrap.objects.create(
            vault=vault, recipient=self.bystander, wrapped_key="ct", hpke_suite={}
        )

        self.bystander.delete()

        self.assertEqual(Vault.objects.filter(pk=vault.pk).count(), 1)
        self.assertEqual(VaultKeyWrap.objects.filter(vault=vault).count(), 1)
        self.assertEqual(VaultEntry.objects.count(), 3)

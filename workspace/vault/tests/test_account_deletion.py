"""What is left of the vault after an account is deleted.

Nothing, is the answer, and the reason is worth stating: every route from a
user into this module is a CASCADE foreign key, and the module owns no blob
storage and registers no delete signal. That makes the guarantee structural
rather than procedural - there is no purge routine to call and forget, and no
place for one to rot.

The shape worth the whole file is an entry sitting in a folder.
``VaultEntry.folder`` is RESTRICT, so the database refuses to orphan it - and
would refuse the whole deletion if the entry were not collected by the same
operation. It is, through its vault. ``test_models.py`` already pins that hop
on its own; what is new here is the graph around it - key wraps, tags, fields,
a second account - and the per-table accounting that says which one leaked.

One consequence is deliberate and not a defect: ``Vault.owner`` is CASCADE, so
deleting an owner destroys the vault for every member it was shared with.
Correct while a vault has a single user; a decision to take again, explicitly,
before sharing ships.
"""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.users.models import UserSetting
from workspace.vault.models import (
    AccountIdentity,
    EntryField,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)
from workspace.vault.tests.factories import make_account, make_key_wrap, make_vault

User = get_user_model()

SIGNATURE = "AXNpZ25hdHVyZQ"

# Asked of the app registry rather than listed by hand: a model added later has
# to be purged too, and a hand-written list would go stale silently - the test
# would keep passing while the new table's rows survived every deletion.
#
# The proxies are dropped on the way out. get_models() returns them, and they
# have no table of their own - counting LoginEntry is counting VaultEntry a
# second time under another name, which would read like extra coverage and be
# none.
VAULT_MODELS = tuple(
    model
    for model in apps.get_app_config("vault").get_models()
    if not model._meta.proxy
)


class AccountDeletionPurgeTests(TestCase):
    def setUp(self):
        self.user, _, _ = make_account(username="owner")
        self.bystander, _, _ = make_account(username="other")

    def _populate(self, owner, *, trashed_entries=False):
        """A vault with a folder tree, tags, entries inside folders and fields."""
        vault = make_vault(owner)
        make_key_wrap(vault, owner)
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
                deleted_at=timezone.now() if trashed_entries else None,
            )
            entry.tags.add(tag)
            EntryField.objects.create(
                entry=entry, field_id="password", encrypted_value="ct"
            )
        return vault

    def _counts(self):
        """One count per table. An aggregate hides which table leaked."""
        return {model.__name__: model.objects.count() for model in VAULT_MODELS}

    def _owned_by(self, user_pk):
        """Every row of the module that hangs off one account, per table.

        By primary key, not by instance: ``delete()`` clears the pk on the
        object it was called with, and a related filter refuses an unsaved
        instance - so the same helper could not be used on both sides of the
        deletion it is meant to measure.
        """
        return {
            AccountIdentity.__name__: AccountIdentity.objects.filter(
                user_id=user_pk
            ).count(),
            Vault.__name__: Vault.objects.filter(owner_id=user_pk).count(),
            VaultKeyWrap.__name__: VaultKeyWrap.objects.filter(
                recipient_id=user_pk
            ).count(),
            VaultFolder.__name__: VaultFolder.objects.filter(
                vault__owner_id=user_pk
            ).count(),
            VaultTag.__name__: VaultTag.objects.filter(vault__owner_id=user_pk).count(),
            VaultEntry.__name__: VaultEntry.objects.filter(
                vault__owner_id=user_pk
            ).count(),
            EntryField.__name__: EntryField.objects.filter(
                entry__vault__owner_id=user_pk
            ).count(),
        }

    def test_nothing_of_the_module_survives_the_account(self):
        self._populate(self.user)
        self.bystander.delete()
        self.assertTrue(all(self._counts().values()), "the fixture populated nothing")

        self.user.delete()

        self.assertEqual(self._counts(), {model.__name__: 0 for model in VAULT_MODELS})

    def test_a_bulk_delete_purges_the_same_way(self):
        """The path an account is actually deleted by today.

        No view deletes an account: it happens through the admin's bulk action,
        which calls ``delete()`` on a queryset rather than on an instance. Both
        go through the same collector, but only one of them is what runs in
        practice - and the guarantee here is structural, so it should not
        matter which. This says it does not.
        """
        self._populate(self.user)
        self.bystander.delete()

        User.objects.filter(pk=self.user.pk).delete()

        self.assertEqual(self._counts(), {model.__name__: 0 for model in VAULT_MODELS})

    def test_trashed_entries_go_with_the_rest(self):
        """``deleted_at`` is a column on the entry, not a state the cascade
        knows about, so a soft-deleted row is as real as any other.

        Only entries carry it - a vault has no trash of its own, and deleting
        one is immediate. The issue's third criterion asks about "a vault in
        the trash", which is a state this model cannot hold; the entries are
        what it can mean.
        """
        self._populate(self.user, trashed_entries=True)
        self.bystander.delete()
        self.assertEqual(VaultEntry.objects.filter(deleted_at__isnull=False).count(), 3)

        self.user.delete()

        self.assertEqual(self._counts(), {model.__name__: 0 for model in VAULT_MODELS})

    def test_the_entry_tag_links_go_with_the_entries(self):
        """The through table has no cascade of its own to look at."""
        vault = self._populate(self.user)
        through = VaultEntry.tags.through
        self.assertTrue(through.objects.filter(vaultentry__vault=vault).exists())

        self.user.delete()

        self.assertEqual(through.objects.count(), 0)

    def test_the_vault_namespaced_settings_go_too(self):
        """Per-user state the module owns without holding the table.

        ``reset_vault`` deletes ``module="vault"`` settings explicitly, which is
        the module's own statement that they are its. They ride
        ``UserSetting.user``'s cascade - but nothing said so until now.
        """
        UserSetting.objects.create(
            user=self.user, module="vault", key="sidebar_collapsed", value=True
        )
        UserSetting.objects.create(
            user=self.bystander, module="vault", key="sidebar_collapsed", value=True
        )

        self.user.delete()

        self.assertEqual(UserSetting.objects.filter(module="vault").count(), 1)
        self.assertFalse(UserSetting.objects.filter(user_id=self.user.pk).exists())

    def test_another_account_keeps_everything(self):
        """The blast radius is one account, which is the other half of the
        claim: a purge that took a neighbour's rows with it would satisfy every
        assertion above. Counted per table for the same reason they are."""
        self._populate(self.user)
        self._populate(self.bystander)
        owner_pk, bystander_pk = self.user.pk, self.bystander.pk
        before = self._owned_by(bystander_pk)
        self.assertTrue(all(before.values()), "the neighbour has nothing to lose")

        self.user.delete()

        self.assertEqual(self._owned_by(bystander_pk), before)
        self.assertEqual(self._owned_by(owner_pk), dict.fromkeys(before, 0))

    def test_a_member_leaving_takes_only_their_wrap(self):
        """The mirror of the owner case, and the reason the owner case is worth
        writing down: a member's departure must not reach the vault."""
        vault = self._populate(self.user)
        make_key_wrap(vault, self.bystander)
        owner_pk, member_pk = self.user.pk, self.bystander.pk

        self.bystander.delete()

        self.assertEqual(Vault.objects.filter(pk=vault.pk).count(), 1)
        # Which wrap survived, not how many: an inverted cascade that destroyed
        # the owner's and kept the member's would pass a count.
        self.assertTrue(
            VaultKeyWrap.objects.filter(vault=vault, recipient_id=owner_pk).exists()
        )
        self.assertFalse(
            VaultKeyWrap.objects.filter(vault=vault, recipient_id=member_pk).exists()
        )
        self.assertEqual(VaultEntry.objects.filter(vault=vault).count(), 3)

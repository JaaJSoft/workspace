"""What is left in the database after an account is deleted.

Nothing, is the answer, and the reason is worth stating: every route from a
user into this module is a CASCADE foreign key, and the module owns no blob
storage and registers no delete signal. That makes the guarantee structural
rather than procedural - there is no purge routine to call and forget, and no
place for one to rot.

The claim is about rows, deliberately. ``get_module_settings`` caches a user's
per-module settings for five minutes and is invalidated only by ``set_setting``
and ``delete_setting``; deleting the account goes through neither, so the vault
preferences of a deleted account stay readable from the cache until the entry
expires. Benign as things stand - primary keys are never reused, so no
successor can read it - but it is the one residue this file cannot claim is
absent.

The shape worth the whole file is an entry sitting in a folder.
``VaultEntry.folder`` is RESTRICT, so the database refuses to orphan it - and
would refuse the whole deletion if the entry were not collected by the same
operation. It is, through its vault. ``test_models.py`` already pins that hop
on its own; what is new here is the graph around it - key wraps, tags, fields,
a second account - and the per-table accounting that says which one leaked.
"""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.users.models import UserSetting
from workspace.users.services.settings import set_setting
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
# include_auto_created, because the default drops the through tables Django
# builds for a ManyToManyField - the one class of table nobody writes down and
# so the one most likely to be forgotten.
#
# The proxies are dropped on the way out. get_models() returns them and they
# have no table of their own, so counting LoginEntry is counting VaultEntry a
# second time under another name: it would read like extra coverage and be
# none.
VAULT_MODELS = tuple(
    model
    for model in apps.get_app_config("vault").get_models(include_auto_created=True)
    if not model._meta.proxy
)

# How each table reaches the account whose rows it holds. Held against
# VAULT_MODELS by a test below, so a new model cannot arrive without someone
# deciding - and writing down - which account owns its rows.
OWNERSHIP_PATHS = {
    AccountIdentity: "user_id",
    Vault: "owner_id",
    VaultKeyWrap: "recipient_id",
    VaultFolder: "vault__owner_id",
    VaultTag: "vault__owner_id",
    VaultEntry: "vault__owner_id",
    EntryField: "entry__vault__owner_id",
    VaultEntry.tags.through: "vaultentry__vault__owner_id",
}


class AccountDeletionPurgeTests(TestCase):
    def setUp(self):
        self.user, _, _ = make_account(username="owner")
        self.bystander, _, _ = make_account(username="other")

    def tearDown(self):
        # set_setting writes through a process-global cache that no TestCase
        # rolls back.
        cache.clear()

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

    def _empty(self):
        return {model.__name__: 0 for model in VAULT_MODELS}

    def _owned_by(self, user_pk):
        """Every row of the module that hangs off one account, per table.

        By primary key, not by instance: ``delete()`` clears the pk on the
        object it was called with, and a related filter refuses an unsaved
        instance - so the same helper could not otherwise be used on both sides
        of the deletion it measures.
        """
        return {
            model.__name__: model.objects.filter(**{path: user_pk}).count()
            for model, path in OWNERSHIP_PATHS.items()
        }

    def test_every_table_has_a_declared_route_to_its_account(self):
        """The tripwire for both helpers above.

        Without it a model added later is absent from ``OWNERSHIP_PATHS`` and
        therefore from both sides of every neighbour comparison, which stays
        green while saying nothing about the new table.
        """
        self.assertEqual(set(OWNERSHIP_PATHS), set(VAULT_MODELS))

    def test_nothing_of_the_module_survives_the_account(self):
        self._populate(self.user)
        self.bystander.delete()
        self.assertTrue(all(self._counts().values()), "the fixture populated nothing")

        self.user.delete()

        self.assertEqual(self._counts(), self._empty())

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

        self.assertEqual(self._counts(), self._empty())

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

        self.assertEqual(self._counts(), self._empty())

    def test_the_entry_tag_links_go_with_the_entries(self):
        """Named on purpose, though VAULT_MODELS now counts the through table
        too: this is where a reader learns the table exists at all."""
        vault = self._populate(self.user)
        through = VaultEntry.tags.through
        self.assertTrue(through.objects.filter(vaultentry__vault=vault).exists())

        self.user.delete()

        self.assertEqual(through.objects.count(), 0)

    def test_the_vault_namespaced_settings_go_too(self):
        """Per-user state the module owns without holding the table.

        ``reset_vault`` deletes ``module="vault"`` settings explicitly, which is
        the module's own statement that they are its. They ride
        ``UserSetting.user``'s cascade - but nothing said so until now. Written
        through ``set_setting`` rather than the ORM, so the row under test is
        the one the application would have produced.
        """
        set_setting(self.user, "vault", "sidebar_collapsed", True)
        set_setting(self.bystander, "vault", "sidebar_collapsed", True)

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
        before = self._owned_by(owner_pk)

        self.bystander.delete()

        # Everything of the owner's, not just the vault row: a cascade that
        # collected their folders on the way out would pass a narrower check.
        self.assertEqual(self._owned_by(owner_pk), before)
        # Which wrap survived, not how many: an inverted cascade that destroyed
        # the owner's and kept the member's would pass a count.
        self.assertTrue(
            VaultKeyWrap.objects.filter(vault=vault, recipient_id=owner_pk).exists()
        )
        self.assertFalse(
            VaultKeyWrap.objects.filter(vault=vault, recipient_id=member_pk).exists()
        )

    def test_deleting_an_owner_takes_a_shared_vault_with_it(self):
        """Deliberate, and the decision to take again before sharing ships.

        ``Vault.owner`` is CASCADE, so an owner's deletion destroys the vault
        for every member it was shared with - the member does not inherit it,
        and is not asked. Correct while a vault has a single user. This is here
        rather than in prose so it fails the day someone implements ownership
        transfer or a soft delete on owner removal, which is exactly when the
        question has to be answered again.
        """
        vault = self._populate(self.user)
        make_key_wrap(vault, self.bystander)
        member_pk = self.bystander.pk

        self.user.delete()

        self.assertFalse(Vault.objects.filter(pk=vault.pk).exists())
        self.assertFalse(VaultKeyWrap.objects.filter(recipient_id=member_pk).exists())
        # The member's own account is untouched - only what hung off the vault.
        self.assertTrue(User.objects.filter(pk=member_pk).exists())
        self.assertTrue(AccountIdentity.objects.filter(user_id=member_pk).exists())

"""The command that sends an account back to onboarding.

It is a development helper, but it is the only way back: the vault has no
"forgot my password" path by design, so an account onboarded once can never
reach the onboarding screen again from the interface. That makes its two
dangerous properties worth pinning - it deletes exactly the scope it was
given, and it refuses to delete anything without a confirmation.
"""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from workspace.users.services.settings import get_setting, set_setting
from workspace.vault.models import (
    AccountIdentity,
    EntryType,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)
from workspace.vault.tests.factories import make_account, make_key_wrap, make_vault

User = get_user_model()


class ResetVaultCommandTests(TestCase):
    def setUp(self):
        self.user = make_account(username="owner")[0]
        self.other = make_account(username="stranger")[0]
        self.vault = make_vault(self.user)
        make_key_wrap(self.vault, self.user)
        self.folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQ", metadata_sig="AQ", position=0
        )
        VaultTag.objects.create(
            vault=self.vault, encrypted_name="AQ", metadata_sig="AQ", color="info"
        )
        VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            folder=self.folder,
            encrypted_name="AQ",
            metadata_sig="AQ",
        )
        set_setting(self.user, "vault", "lock_after_minutes", 15)
        self.other_vault = make_vault(self.other)

    def tearDown(self):
        cache.clear()

    def _run(self, **options):
        out = StringIO()
        call_command("reset_vault", stdout=out, stderr=StringIO(), **options)
        return out.getvalue()

    def test_a_named_account_loses_everything_it_had(self):
        self._run(username="owner", yes=True)
        self.assertFalse(AccountIdentity.objects.filter(user=self.user).exists())
        self.assertFalse(Vault.objects.filter(owner=self.user).exists())
        self.assertFalse(VaultKeyWrap.objects.filter(recipient=self.user).exists())
        self.assertFalse(VaultEntry.objects.filter(vault=self.vault).exists())
        self.assertFalse(VaultFolder.objects.filter(vault=self.vault).exists())
        self.assertFalse(VaultTag.objects.filter(vault=self.vault).exists())
        self.assertIsNone(
            get_setting(self.user, "vault", "lock_after_minutes", default=None)
        )

    def test_another_account_keeps_its_own(self):
        """The scope is the whole point: run against one demo account, the
        command must not empty the vault of whoever else uses the database."""
        self._run(username="owner", yes=True)
        self.assertTrue(AccountIdentity.objects.filter(user=self.other).exists())
        self.assertTrue(Vault.objects.filter(owner=self.other).exists())

    def test_without_a_user_it_resets_every_account(self):
        self._run(yes=True)
        self.assertEqual(AccountIdentity.objects.count(), 0)
        self.assertEqual(Vault.objects.count(), 0)

    def test_a_dry_run_reports_and_deletes_nothing(self):
        output = self._run(username="owner", dry_run=True)
        self.assertIn("Would delete", output)
        self.assertTrue(Vault.objects.filter(owner=self.user).exists())
        self.assertTrue(AccountIdentity.objects.filter(user=self.user).exists())

    def test_the_summary_names_what_it_found(self):
        output = self._run(username="owner", dry_run=True)
        for fragment in ("1 identities", "1 vaults", "1 entries", "1 settings"):
            self.assertIn(fragment, output)

    def test_an_unknown_user_is_refused_rather_than_read_as_everyone(self):
        with self.assertRaises(CommandError):
            self._run(username="nobody", yes=True)
        self.assertTrue(Vault.objects.filter(owner=self.user).exists())

    def test_an_empty_scope_says_so_and_stops(self):
        User.objects.create_user(username="fresh", password="pw")
        output = self._run(username="fresh", yes=True)
        self.assertIn("Nothing to reset", output)

    @patch("builtins.input", return_value="no")
    def test_a_refused_confirmation_deletes_nothing(self, _prompt):
        with self.assertRaises(CommandError):
            self._run(username="owner")
        self.assertTrue(Vault.objects.filter(owner=self.user).exists())
        self.assertTrue(AccountIdentity.objects.filter(user=self.user).exists())

    @patch("builtins.input", return_value="yes")
    def test_a_typed_confirmation_is_accepted(self, _prompt):
        self._run(username="owner")
        self.assertFalse(Vault.objects.filter(owner=self.user).exists())

    def test_the_browser_half_of_the_reset_is_printed(self):
        """A remembered recovery key outlives the database, and an account
        with no identity plus a remembered key is a state the interface never
        produces on its own - so the command has to say what to run there."""
        self.assertIn("vault.secret-key", self._run(username="owner", yes=True))

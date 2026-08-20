from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.vault.models import AccountIdentity, Vault, VaultEntry, VaultKeyWrap

User = get_user_model()


class VaultAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.identity = AccountIdentity.objects.create(
            user=cls.admin,
            kdf_salt="c2FsdA",
            kex_public="AWtleA",
            sig_public="AXNpZw",
            wrapped_kex_priv="AXdyYXA",
            wrapped_sig_priv="AXdyYXA",
            sig_over_kex_pub="AXNpZw",
            state=AccountIdentity.State.ACTIVE,
        )
        cls.vault = Vault.objects.create(
            owner=cls.admin,
            encrypted_name="AQEBAAEMbmFtZQ",
            metadata_sig="AXNpZ25hdHVyZQ",
        )
        cls.wrap = VaultKeyWrap.objects.create(
            vault=cls.vault, recipient=cls.admin, wrapped_key="AXdyYXBwZWQ"
        )
        cls.entry = VaultEntry.objects.create(
            vault=cls.vault,
            encrypted_name="AQEBAAEMbmFtZQ",
            metadata_sig="AXNpZ25hdHVyZQ",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_change_lists_render(self):
        for url_name in (
            "admin:vault_accountidentity_changelist",
            "admin:vault_vault_changelist",
            "admin:vault_vaultkeywrap_changelist",
            "admin:vault_vaultentry_changelist",
        ):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)

    def test_encrypted_rows_cannot_be_added_or_edited(self):
        for url_name in (
            "admin:vault_accountidentity_add",
            "admin:vault_vault_add",
            "admin:vault_vaultkeywrap_add",
            "admin:vault_vaultentry_add",
        ):
            self.assertEqual(
                self.client.get(reverse(url_name)).status_code, 403, url_name
            )

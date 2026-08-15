from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import (
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultRole,
)
from workspace.vault.queries import (
    accessible_entries_q,
    get_vault_role,
    user_vault_ids,
    visible_folders,
)

User = get_user_model()


class AccessHelperTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.stranger = User.objects.create_user(username="stranger", password="pw")
        self.vault = Vault.objects.create(
            owner=self.owner,
            encrypted_name="AQEBAAEFdmF1bHQ",
            metadata_sig="AXNpZ25hdHVyZQ",
        )

    def test_owner_sees_their_vault(self):
        self.assertEqual(user_vault_ids(self.owner), [self.vault.uuid])

    def test_stranger_sees_nothing(self):
        self.assertEqual(user_vault_ids(self.stranger), [])

    def test_a_key_wrap_grants_access(self):
        VaultKeyWrap.objects.create(
            vault=self.vault,
            recipient=self.stranger,
            wrapped_key="ZW5jfHdyYXA",
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )
        self.assertEqual(user_vault_ids(self.stranger), [self.vault.uuid])

    def test_owning_and_holding_a_wrap_is_not_counted_twice(self):
        VaultKeyWrap.objects.create(
            vault=self.vault,
            recipient=self.owner,
            wrapped_key="ZW5jfHdyYXA",
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )
        self.assertEqual(user_vault_ids(self.owner), [self.vault.uuid])

    def test_roles(self):
        self.assertEqual(get_vault_role(self.owner, self.vault), VaultRole.OWNER)
        self.assertIsNone(get_vault_role(self.stranger, self.vault))
        VaultKeyWrap.objects.create(
            vault=self.vault,
            recipient=self.stranger,
            wrapped_key="ZW5jfHdyYXA",
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )
        self.assertEqual(get_vault_role(self.stranger, self.vault), VaultRole.MEMBER)

    def test_accessible_entries_keeps_trashed_rows(self):
        live = VaultEntry.objects.create(
            vault=self.vault,
            encrypted_name="AQEBAAEEbGl2ZQ",
            metadata_sig="AXNpZ25hdHVyZQ",
        )
        trashed = VaultEntry.objects.create(
            vault=self.vault,
            encrypted_name="AQEBAAEHdHJhc2hlZA",
            metadata_sig="AXNpZ25hdHVyZQ",
            deleted_at=timezone.now(),
        )
        visible = set(
            VaultEntry.objects.filter(accessible_entries_q(self.owner)).values_list(
                "uuid", flat=True
            )
        )
        self.assertEqual(visible, {live.uuid, trashed.uuid})
        self.assertEqual(
            VaultEntry.objects.filter(accessible_entries_q(self.stranger)).count(), 0
        )

    def test_visible_folders_are_scoped_to_an_accessible_vault(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGZm9sZGVy"
        )
        self.assertEqual(list(visible_folders(self.owner, self.vault)), [folder])
        self.assertEqual(list(visible_folders(self.stranger, self.vault)), [])

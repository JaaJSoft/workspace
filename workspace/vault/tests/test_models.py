from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.vault.models import (
    AccountIdentity,
    Vault,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)

User = get_user_model()


def make_identity(user, **overrides):
    """An identity with placeholder blobs - the server never reads them."""
    fields = {
        "user": user,
        "kdf_params": {"v": "1.3", "m": 65536, "t": 3, "p": 2},
        "kdf_salt": "c2FsdA",
        "kex_public": "AWtleA",
        "sig_public": "AXNpZw",
        "wrapped_kex_priv": "AQEAAAAMd3JhcHBlZC1rZXg",
        "wrapped_sig_priv": "AQEAAAAMd3JhcHBlZC1zaWc",
        "sig_over_kex_pub": "AXNpZ25hdHVyZQ",
    }
    fields.update(overrides)
    return AccountIdentity.objects.create(**fields)


class AccountIdentityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")

    def test_defaults_to_argon2id_and_pending(self):
        identity = make_identity(self.user)
        self.assertEqual(identity.kdf_algo, "argon2id")
        self.assertEqual(identity.state, AccountIdentity.State.PENDING)

    def test_one_identity_per_user(self):
        make_identity(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_identity(self.user)

    def test_reachable_from_the_user(self):
        identity = make_identity(self.user)
        self.user.refresh_from_db()
        self.assertEqual(self.user.vault_identity, identity)

    def test_deleted_with_its_user(self):
        make_identity(self.user)
        self.user.delete()
        self.assertEqual(AccountIdentity.objects.count(), 0)


def make_vault(owner, **overrides):
    fields = {
        "owner": owner,
        "encrypted_name": "AQEBAAEMbmFtZQ",
        "metadata_sig": "AXNpZ25hdHVyZQ",
    }
    fields.update(overrides)
    return Vault.objects.create(**fields)


class VaultTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")

    def test_encrypts_uris_by_default(self):
        vault = make_vault(self.user)
        self.assertTrue(vault.encrypt_uris)
        self.assertEqual(vault.key_version, 1)
        self.assertFalse(vault.is_favorite)

    def test_reachable_from_the_owner(self):
        vault = make_vault(self.user)
        self.assertEqual(list(self.user.vaults.all()), [vault])

    def test_deleted_with_its_owner(self):
        make_vault(self.user)
        self.user.delete()
        self.assertEqual(Vault.objects.count(), 0)


class VaultKeyWrapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.vault = make_vault(self.user)

    def _wrap(self, recipient):
        return VaultKeyWrap.objects.create(
            vault=self.vault,
            recipient=recipient,
            wrapped_key="ZW5jfHdyYXBwZWQ",
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )

    def test_one_wrap_per_recipient_and_vault(self):
        self._wrap(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._wrap(self.user)

    def test_same_recipient_may_hold_wraps_for_several_vaults(self):
        self._wrap(self.user)
        other = make_vault(self.user)
        VaultKeyWrap.objects.create(
            vault=other,
            recipient=self.user,
            wrapped_key="ZW5jfG90aGVy",
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )
        self.assertEqual(self.user.vault_key_wraps.count(), 2)


class VaultFolderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.vault = make_vault(self.user)

    def _folder(self, name="AQEBAAEGZm9sZGVy", **overrides):
        return VaultFolder.objects.create(
            vault=self.vault, encrypted_name=name, **overrides
        )

    def test_nests_under_a_parent(self):
        parent = self._folder()
        child = self._folder(parent=parent)
        self.assertEqual(list(parent.children.all()), [child])

    def test_rejects_being_its_own_parent(self):
        folder = self._folder()
        folder.parent = folder
        with self.assertRaises(ValidationError):
            folder.clean()

    def test_rejects_a_parent_cycle(self):
        grandparent = self._folder()
        parent = self._folder(parent=grandparent)
        grandparent.parent = parent
        with self.assertRaises(ValidationError):
            grandparent.clean()

    def test_rejects_a_parent_from_another_vault(self):
        other_vault = make_vault(self.user)
        outsider = VaultFolder.objects.create(
            vault=other_vault, encrypted_name="AQEBAAEGb3RoZXI"
        )
        folder = self._folder()
        folder.parent = outsider
        with self.assertRaises(ValidationError):
            folder.clean()

    def test_deleted_with_its_vault(self):
        self._folder()
        self.vault.delete()
        self.assertEqual(VaultFolder.objects.count(), 0)


class VaultTagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.vault = make_vault(self.user)

    def test_defaults_to_a_neutral_color(self):
        tag = VaultTag.objects.create(vault=self.vault, encrypted_name="AQEBAAEDdGFn")
        self.assertEqual(tag.color, "neutral")
        self.assertEqual(list(self.vault.tags.all()), [tag])

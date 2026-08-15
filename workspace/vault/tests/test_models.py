from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase

from workspace.vault.models import (
    AccountIdentity,
    EntryField,
    EntryType,
    Vault,
    VaultEntry,
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

    def test_string_representation_names_the_user(self):
        identity = make_identity(self.user)
        self.assertEqual(str(identity), f"Vault identity of {self.user.pk}")


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

    def test_starts_on_the_first_key_generation(self):
        vault = make_vault(self.user)
        self.assertEqual(vault.key_version, 1)
        self.assertFalse(vault.is_favorite)

    def test_rejects_an_unsigned_vault(self):
        """Django enforces blank=False only through full_clean(), which no
        caller runs on its own - so an unsigned vault, exactly what a hostile
        server would insert, must be refused by the database itself.
        """
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_vault(self.user, metadata_sig="")

    def test_reachable_from_the_owner(self):
        vault = make_vault(self.user)
        self.assertEqual(list(self.user.vaults.all()), [vault])

    def test_deleted_with_its_owner(self):
        make_vault(self.user)
        self.user.delete()
        self.assertEqual(Vault.objects.count(), 0)

    def test_string_representation_names_the_vault(self):
        vault = make_vault(self.user)
        self.assertEqual(str(vault), f"Vault {vault.uuid}")


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

    def test_string_representation_names_the_vault_and_recipient(self):
        wrap = self._wrap(self.user)
        self.assertEqual(str(wrap), f"Key wrap of {self.vault.uuid} for {self.user.pk}")

    def test_deleted_with_its_vault(self):
        self._wrap(self.user)
        self.vault.delete()
        self.assertEqual(VaultKeyWrap.objects.count(), 0)

    def test_deleted_with_its_recipient(self):
        other_vault = make_vault(self.user)
        recipient = User.objects.create_user(username="recipient", password="pw")
        VaultKeyWrap.objects.create(
            vault=other_vault,
            recipient=recipient,
            wrapped_key="ZW5jfHdyYXBwZWQ",
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )
        recipient.delete()
        self.assertEqual(VaultKeyWrap.objects.count(), 0)


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

    def test_accepts_no_parent(self):
        folder = self._folder()
        folder.clean()

    def test_accepts_a_valid_parent(self):
        parent = self._folder()
        child = self._folder(parent=parent)
        child.clean()

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

    def test_string_representation_names_the_folder(self):
        folder = self._folder()
        self.assertEqual(str(folder), f"Folder {folder.uuid}")


class VaultTagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.vault = make_vault(self.user)

    def test_defaults_to_a_neutral_color(self):
        tag = VaultTag.objects.create(vault=self.vault, encrypted_name="AQEBAAEDdGFn")
        self.assertEqual(tag.color, "neutral")
        self.assertEqual(list(self.vault.tags.all()), [tag])

    def test_string_representation_names_the_tag(self):
        tag = VaultTag.objects.create(vault=self.vault, encrypted_name="AQEBAAEDdGFn")
        self.assertEqual(str(tag), f"Tag {tag.uuid}")

    def test_deleted_with_its_vault(self):
        VaultTag.objects.create(vault=self.vault, encrypted_name="AQEBAAEDdGFn")
        self.vault.delete()
        self.assertEqual(VaultTag.objects.count(), 0)


class VaultEntryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.vault = make_vault(self.user)

    def _entry(self, **overrides):
        fields = {
            "vault": self.vault,
            "encrypted_name": "AQEBAAEFZW50cnk",
            "metadata_sig": "AXNpZ25hdHVyZQ",
        }
        fields.update(overrides)
        return VaultEntry.objects.create(**fields)

    def test_rejects_an_unsigned_entry(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._entry(metadata_sig="")

    def test_defaults_to_a_login_entry(self):
        entry = self._entry()
        self.assertEqual(entry.type, EntryType.LOGIN)
        self.assertEqual(entry.key_version, 1)
        self.assertEqual(entry.entry_version, 1)
        self.assertIsNone(entry.deleted_at)

    def test_blocks_deleting_a_folder_that_still_holds_entries(self):
        """folder_id is plaintext but signed (design spec §3.5). A SET_NULL
        would let the database rewrite signed data behind the client's back,
        and the client would then flag a legitimate folder deletion as
        tampering. Emptying the folder is the API's job, with the entries
        re-signed client side.
        """
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGZm9sZGVy"
        )
        self._entry(folder=folder)
        with self.assertRaises(RestrictedError), transaction.atomic():
            folder.delete()

    def test_blocks_deleting_a_parent_whose_subtree_holds_entries(self):
        parent = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGcGFyZW50"
        )
        child = VaultFolder.objects.create(
            vault=self.vault, parent=parent, encrypted_name="AQEBAAEFY2hpbGQ"
        )
        self._entry(folder=child)
        with self.assertRaises(RestrictedError), transaction.atomic():
            parent.delete()

    def test_an_emptied_folder_can_be_deleted(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGZm9sZGVy"
        )
        entry = self._entry(folder=folder)
        entry.folder = None
        entry.save(update_fields=["folder"])
        folder.delete()
        self.assertEqual(VaultFolder.objects.count(), 0)
        self.assertEqual(VaultEntry.objects.count(), 1)

    def test_deleting_the_vault_still_cascades_through_a_folder(self):
        """RESTRICT must not turn a vault deletion into an error: the entries
        go with the vault by their own cascade, so the folder is free to go.
        """
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGZm9sZGVy"
        )
        self._entry(folder=folder)
        self.vault.delete()
        self.assertEqual(VaultEntry.objects.count(), 0)
        self.assertEqual(VaultFolder.objects.count(), 0)

    def test_deleting_the_account_still_purges_a_foldered_entry(self):
        """Account deletion is a GDPR requirement, and RESTRICT is exactly the
        kind of guard that can break it two cascade hops away from where it is
        declared. Pinned here so PR 14 inherits a working baseline.
        """
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGZm9sZGVy"
        )
        self._entry(folder=folder)
        self.user.delete()
        self.assertEqual(Vault.objects.count(), 0)
        self.assertEqual(VaultFolder.objects.count(), 0)
        self.assertEqual(VaultEntry.objects.count(), 0)

    def test_carries_tags(self):
        tag = VaultTag.objects.create(vault=self.vault, encrypted_name="AQEBAAEDdGFn")
        entry = self._entry()
        entry.tags.add(tag)
        self.assertEqual(list(tag.entries.all()), [entry])

    def test_accepts_a_tag_from_another_vault_today(self):
        """Known limitation: attaching a tag from a different vault raises
        nothing today. ``clean()`` cannot check the ``tags`` M2M because
        Django only validates a many-to-many once the row exists, so
        enforcement is deferred to the entry API (PR 7, design spec §9).

        This pins the current permissive behavior so that PR 7 fails here
        loudly instead of silently leaving the hole open.
        """
        other_vault = make_vault(self.user)
        outsider_tag = VaultTag.objects.create(
            vault=other_vault, encrypted_name="AQEBAAEDdGFn"
        )
        entry = self._entry()
        entry.tags.add(outsider_tag)
        self.assertEqual(list(entry.tags.all()), [outsider_tag])

    def test_deleted_with_its_vault(self):
        self._entry()
        self.vault.delete()
        self.assertEqual(VaultEntry.objects.count(), 0)

    def test_string_representation_names_the_entry(self):
        entry = self._entry()
        self.assertEqual(str(entry), f"Entry {entry.uuid}")

    def test_accepts_no_folder(self):
        entry = self._entry()
        entry.clean()

    def test_rejects_a_folder_from_another_vault(self):
        other_vault = make_vault(self.user)
        outsider = VaultFolder.objects.create(
            vault=other_vault, encrypted_name="AQEBAAEGb3RoZXI"
        )
        entry = self._entry()
        entry.folder = outsider
        with self.assertRaises(ValidationError):
            entry.clean()

    def test_accepts_a_folder_from_its_own_vault(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQEBAAEGZm9sZGVy"
        )
        entry = self._entry(folder=folder)
        entry.clean()


class EntryFieldTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.vault = make_vault(self.user)
        self.entry = VaultEntry.objects.create(
            vault=self.vault,
            encrypted_name="AQEBAAEFZW50cnk",
            metadata_sig="AXNpZ25hdHVyZQ",
        )

    def _field(self, field_id="password"):
        return EntryField.objects.create(
            entry=self.entry, field_id=field_id, encrypted_value="AQEBAAEFdmFsdWU"
        )

    def test_one_row_per_field_id_and_entry(self):
        self._field()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._field()

    def test_custom_fields_live_alongside_reserved_ones(self):
        self._field("password")
        self._field("custom:recovery-code")
        self.assertEqual(
            set(self.entry.fields.values_list("field_id", flat=True)),
            {"password", "custom:recovery-code"},
        )

    def test_deleted_with_its_entry(self):
        self._field()
        self.entry.delete()
        self.assertEqual(EntryField.objects.count(), 0)

    def test_string_representation_names_the_field_and_entry(self):
        field = self._field()
        self.assertEqual(str(field), f"password of {self.entry.uuid}")

    def test_rejects_the_name_and_notes_field_ids(self):
        """`name` and `notes` derive the same associated data as
        VaultEntry.encrypted_name/encrypted_notes (design spec §3.4); the
        database constraint closes the permutation a unique(entry, field_id)
        cannot, since the two ciphertexts live in different tables.
        """
        for reserved in ("name", "notes"):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self._field(reserved)

    def test_accepts_a_custom_field_named_name(self):
        field = self._field("custom:name")
        self.assertEqual(field.field_id, "custom:name")

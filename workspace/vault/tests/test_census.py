from collections import Counter

from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import (
    AccountIdentity,
    EntryField,
    EntryType,
    VaultEntry,
    VaultFolder,
    VaultTag,
)
from workspace.vault.services import census
from workspace.vault.tests.factories import (
    HPKE_SUITE,
    make_account,
    make_key_wrap,
    make_vault,
    sealed,
)

CURRENT_HPKE = {**HPKE_SUITE, "format": 2}


class MarkTests(TestCase):
    def test_ciphertext_marks_read_format_and_aead(self):
        self.assertEqual(
            census.ciphertext_marks(sealed("x")[:4]), [("format", 2), ("aead", 1)]
        )
        self.assertEqual(
            census.ciphertext_marks(sealed("x", 1)[:4]), [("format", 1), ("aead", 1)]
        )
        self.assertEqual(census.ciphertext_marks(""), [])
        self.assertEqual(census.ciphertext_marks("!!"), [("format", "?")])

    def test_signature_and_pubkey_marks_read_the_prefix(self):
        self.assertEqual(census.signature_marks("AXNp"), [("signature", 1)])
        self.assertEqual(census.pubkey_marks("Ag"), [("pubkey", 2)])

    def test_hpke_marks(self):
        self.assertEqual(census.hpke_marks(HPKE_SUITE), [("hpke", 1)])
        self.assertEqual(census.hpke_marks(CURRENT_HPKE), [("hpke", 2)])
        self.assertEqual(
            census.hpke_marks({**HPKE_SUITE, "format": True}), [("hpke", "?")]
        )
        self.assertEqual(
            census.hpke_marks({**HPKE_SUITE, "kem_id": 1.0}), [("hpke", "?")]
        )
        self.assertEqual(census.hpke_marks("nope"), [("hpke", "?")])

    def test_is_stale(self):
        self.assertTrue(census.is_stale([("format", 1), ("aead", 1)]))
        self.assertFalse(census.is_stale([("format", 2), ("aead", 1)]))
        self.assertFalse(census.is_stale([("format", "?")]))

    def test_an_hpke_format_is_stale_only_while_declared_superseded(self):
        self.assertTrue(census.is_stale(census.hpke_marks(HPKE_SUITE)))
        self.assertFalse(census.is_stale(census.hpke_marks(CURRENT_HPKE)))
        self.assertFalse(
            census.is_stale(census.hpke_marks({**HPKE_SUITE, "format": 7}))
        )


class StaleRowsTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.vault = make_vault(self.user)
        make_key_wrap(self.vault, self.user, hpke_suite=CURRENT_HPKE)

    def _entry(self, name_format=2, field_format=2, deleted=False):
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("n", name_format),
            metadata_sig="AQ",
        )
        EntryField.objects.create(
            entry=entry, field_id="password", encrypted_value=sealed("p", field_format)
        )
        if deleted:
            VaultEntry.objects.filter(pk=entry.pk).update(deleted_at=timezone.now())
        return entry

    def test_a_clean_account_lists_nothing(self):
        self._entry()
        self.assertEqual(census.stale_rows(self.user), [])

    def test_a_stale_field_lists_its_entry(self):
        entry = self._entry(field_format=1)
        [listed] = census.stale_rows(self.user)
        self.assertEqual(listed["entries"], [str(entry.uuid)])

    def test_trashed_rows_are_listed(self):
        entry = self._entry(name_format=1, deleted=True)
        self.assertEqual(census.stale_rows(self.user)[0]["entries"], [str(entry.uuid)])

    def test_metadata_only_for_the_owner(self):
        self.vault.encrypted_name = sealed("v", 1)
        self.vault.save(update_fields=["encrypted_name"])
        member, _, _ = make_account("member")
        make_key_wrap(self.vault, member, hpke_suite=CURRENT_HPKE)
        self.assertTrue(census.stale_rows(self.user)[0]["metadata"])
        self.assertEqual(census.stale_rows(member), [])

    def test_wrap_is_the_callers_own(self):
        member, _, _ = make_account("member")
        make_key_wrap(self.vault, member, hpke_suite=HPKE_SUITE)
        self.assertEqual(census.stale_rows(self.user), [])
        self.assertTrue(census.stale_rows(member)[0]["wrap"])

    def test_folders_and_tags(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name=sealed("f", 1), metadata_sig="AQ"
        )
        tag = VaultTag.objects.create(
            vault=self.vault, encrypted_name=sealed("t", 1), metadata_sig="AQ"
        )
        [listed] = census.stale_rows(self.user)
        self.assertEqual(listed["folders"], [str(folder.uuid)])
        self.assertEqual(listed["tags"], [str(tag.uuid)])

    def test_an_empty_description_is_not_counted(self):
        self.assertEqual(self.vault.encrypted_description, "")
        self.assertEqual(census.stale_rows(self.user), [])


class CensusTests(TestCase):
    def test_counts_every_column_without_a_state_filter(self):
        user, _, identity = make_account("owner")
        AccountIdentity.objects.filter(pk=identity.pk).update(
            wrapped_kex_priv=sealed("k", 1),
            wrapped_sig_priv=sealed("s", 1),
            kex_public="AQ",
            sig_over_kex_pub="AQ",
            kdf_algo="argon2id",
        )
        _, _, pending = make_account("pending")
        AccountIdentity.objects.filter(pk=pending.pk).update(
            state=AccountIdentity.State.PENDING, wrapped_kex_priv=sealed("k", 1)
        )
        vault = make_vault(user)
        make_key_wrap(vault, user)
        counts = census.census()
        self.assertEqual(counts[("format", 1)], 3)
        self.assertEqual(counts[("hpke", 1)], 1)
        self.assertEqual(counts[("kdf", "argon2id")], 2)
        self.assertIsInstance(counts, Counter)

    def test_a_trashed_entry_is_counted(self):
        user, _, _ = make_account("owner")
        vault = make_vault(user)
        VaultEntry.objects.create(
            vault=vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("n", 1),
            metadata_sig="AQ",
            deleted_at=timezone.now(),
        )
        self.assertEqual(census.census()[("format", 1)], 1)

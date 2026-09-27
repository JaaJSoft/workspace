"""The dispatch, proven where it runs: a real page reading rows it did not write.

The reference implementation writes rows under the test AEAD and under format 1;
the page, running the production bundle plus the test-suite script, must read
them beside its own format-2 rows. Then the negative half: bytes naming an
algorithm nobody implements read as "needs a newer version", never as a wrong
password or as tampering.

Rows are written straight into the database for the same reason the forged
signature in test_browser.py is: the API refuses a ciphertext naming an AEAD it
does not know, so only the database can stand in for a newer build's writes.
"""

import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from workspace.vault.models import AccountIdentity, Vault, VaultEntry, VaultKeyWrap
from workspace.vault.tests.reference import ad, metadata, primitives, suites, wire
from workspace.vault.tests.reference.encoding import from_base64url, to_base64url

from .test_browser import GOOD_PASSWORD, VaultBrowserCase

UNSUPPORTED_BANNER = "inline-alert:has-text('a newer version of the app')"
TAMPERED_BANNER = "inline-alert:has-text('removed from the list')"
# An id no manifest declares, so no build of this app can open it.
UNKNOWN_AEAD_ID = 0x07


class CryptoAgilityBrowserTests(VaultBrowserCase):
    def test_rows_under_three_constructions_list_side_by_side(self):
        self._open_vault()
        self._write_entry_as_reference("Format 1", format_version=1)
        self._write_entry_as_reference("Test aead", aead_id=wire.AEAD_TEST_CTR_HMAC)
        self._create_entry("Format 2", "octocat", "hunter2")

        self._reload_and_unlock()
        # The listing renders once every row has been read, so the page's own
        # row appearing means the other two were decided too.
        self.page.wait_for_selector("tbody tr:has-text('Format 2')", timeout=30000)
        self.assertEqual(
            sorted(self._listed_entry_names()), ["Format 1", "Format 2", "Test aead"]
        )
        self.assertEqual(self._banner_count(TAMPERED_BANNER), 0)
        self.assertEqual(self._banner_count(UNSUPPORTED_BANNER), 0)

    def test_an_envelope_under_an_unknown_kdf_asks_for_a_reload(self):
        self._onboard()
        AccountIdentity.objects.filter(user=self.user).update(kdf_algo="scrypt")

        self.page.reload()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill("input[autocomplete='current-password']", GOOD_PASSWORD)
        self.page.click("button:has-text('Unlock')")
        error = self.page.locator("inline-alert[type='error']:visible").first
        error.wait_for(timeout=60000)
        text = error.inner_text()
        self.assertIn("newer version of the app", text)
        # Both wordings of a refused password: the remembered-key variant is
        # the one this account would get, since onboarding ticked "remember".
        self.assertNotIn("does not open this account", text)
        self.assertNotIn("did not open this account", text)

    def test_a_row_under_an_unknown_aead_is_counted_as_needing_a_reload(self):
        self._open_vault()
        self._create_entry("From the future", "octocat", "hunter2")
        entry = VaultEntry.objects.get(vault__uuid=self.vault_uuid)
        self._rewrite_header_byte(entry, "encrypted_name", 1, UNKNOWN_AEAD_ID)

        self._reload_and_unlock()
        # Either banner: waiting on the right one alone would turn a row
        # misread as tampered into a timeout instead of a failed count.
        self.page.wait_for_selector(
            f"{UNSUPPORTED_BANNER}, {TAMPERED_BANNER}", timeout=30000
        )
        self.assertEqual(self._banner_count(UNSUPPORTED_BANNER), 1)
        self.assertEqual(self._banner_count(TAMPERED_BANNER), 0)
        self.assertEqual(self._listed_entry_names(), [])

    # ---- the page ----------------------------------------------------------

    def _reload_and_unlock(self):
        self.page.reload()
        self._unlock()
        self.page.wait_for_selector("text=All entries", timeout=30000)

    def _listed_entry_names(self):
        return self.page.locator(
            "[data-testid='entry-list'] tbody tr span.font-medium"
        ).all_inner_texts()

    def _banner_count(self, banner):
        """The number a banner states, 0 when it is not rendered at all."""
        found = self.page.locator(banner)
        if not found.count():
            return 0
        return int(found.first.locator("b > span").first.inner_text())

    # ---- the account, as the reference sees it ----------------------------

    def _account_keys(self):
        """The account's private keys, opened from the password and the
        recovery key onboarding displayed, under the parameters it stored."""
        identity = AccountIdentity.objects.get(user=self.user)
        account_uuid = str(identity.uuid)
        amk = primitives.derive_amk(
            GOOD_PASSWORD,
            primitives.crockford_decode(self.secret),
            from_base64url(identity.kdf_salt),
            identity.kdf_params,
        )
        unwrap = primitives.hkdf(amk, ad.unwrap_info())
        kex_priv = primitives.aead_open(
            unwrap,
            from_base64url(identity.wrapped_kex_priv),
            ad.kex_priv_ad(account_uuid),
        )
        sig_priv = primitives.aead_open(
            unwrap,
            from_base64url(identity.wrapped_sig_priv),
            ad.sig_priv_ad(account_uuid),
        )
        return (
            account_uuid,
            X25519PrivateKey.from_private_bytes(kex_priv),
            Ed25519PrivateKey.from_private_bytes(sig_priv),
        )

    def _vault_key(self, vault, account_uuid, kex_priv):
        wrap = VaultKeyWrap.objects.get(vault=vault, recipient=self.user)
        return primitives.hpke_open(
            kex_priv,
            ad.vault_key_info(str(vault.uuid), account_uuid, wrap.hpke_suite),
            from_base64url(wrap.wrapped_key),
            hpke_suite=wrap.hpke_suite,
        )

    def _signature(self, entry, account_uuid, sig_priv, *, tag_uuids, fields):
        payload = metadata.entry_metadata_payload(
            entry_uuid=str(entry.uuid),
            vault_uuid=str(entry.vault_id),
            signer_account_uuid=account_uuid,
            entry_type=entry.type,
            folder_uuid=entry.folder_id,
            encrypted_name=entry.encrypted_name,
            encrypted_notes=entry.encrypted_notes,
            key_version=entry.key_version,
            entry_version=entry.entry_version,
            is_favorite=entry.is_favorite,
            tag_uuids=tag_uuids,
            fields=fields,
        )
        return to_base64url(primitives.sign(sig_priv, payload))

    def _write_entry_as_reference(self, name, *, format_version=None, aead_id=None):
        """An entry whose name the reference sealed, signed by the account.

        Sealed under the parameters given rather than under the current suite:
        this is how a row written by another build - older or newer - lands in
        the table.
        """
        account_uuid, kex_priv, sig_priv = self._account_keys()
        vault = Vault.objects.get(uuid=self.vault_uuid)
        entry = VaultEntry(vault=vault, key_version=vault.key_version)
        entry_key = primitives.hkdf(
            self._vault_key(vault, account_uuid, kex_priv),
            ad.entry_key_info(str(entry.uuid)),
        )
        aead = suites.CURRENT_SUITE["aead_id"] if aead_id is None else aead_id
        entry.encrypted_name = to_base64url(
            primitives.aead_seal(
                entry_key,
                name.encode("utf-8"),
                ad.entry_field_ad(str(entry.uuid), "name"),
                iv=os.urandom(suites.entry("aead", aead)["iv_length"]),
                key_version=vault.key_version,
                kdf_id=wire.KDF_HKDF_SHA256,
                format_version=format_version,
                aead_id=aead,
            )
        )
        # An unsaved row has no tags or fields to ask for, and this one has
        # neither.
        entry.metadata_sig = self._signature(
            entry, account_uuid, sig_priv, tag_uuids=[], fields={}
        )
        entry.save()

    def _rewrite_header_byte(self, entry, field, index, value):
        """Patch one header byte of a stored ciphertext, then sign the row
        again with the account's own key.

        The page verifies a row before it opens anything in it, so an edit
        left unsigned would read as tampering whatever the byte said. Signed
        again, the byte is the only thing wrong with the row.
        """
        raw = bytearray(from_base64url(getattr(entry, field)))
        raw[index] = value
        setattr(entry, field, to_base64url(bytes(raw)))
        account_uuid, _, sig_priv = self._account_keys()
        entry.metadata_sig = self._signature(
            entry,
            account_uuid,
            sig_priv,
            tag_uuids=entry.tags.values_list("uuid", flat=True),
            fields=dict(entry.fields.values_list("field_id", "encrypted_value")),
        )
        VaultEntry.objects.filter(uuid=entry.uuid).update(
            **{field: getattr(entry, field), "metadata_sig": entry.metadata_sig}
        )

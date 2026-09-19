"""The Python reference opens an account this build never wrote.

The parity vectors prove the bundle and the reference agree today. They are
regenerated from the current code, so a deliberate change to a primitive
rewrites them and keeps CI green. This file is the other half: fixed bytes,
never regenerated, opened by an implementation that shares no line with the
browser's.
"""

import json

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from django.test import SimpleTestCase

from . import compat
from .reference import ad, archive, encoding, metadata, primitives

# Every published corpus version a replay test in this file reads. Task 5
# checks this against compat.versions() so a new corpus directory can never
# go unread.
COVERED = ["v1"]


def _only(rows, model):
    """The single row of *model*, failing loudly on zero or on more than one.

    A replay that loops over an empty list passes without opening anything -
    this is what stops that.
    """
    matches = [row for row in rows if row["model"] == model]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {model} row, found {len(matches)}")
    return matches[0]


def _all(rows, model):
    matches = [row for row in rows if row["model"] == model]
    if not matches:
        raise AssertionError(f"expected at least one {model} row, found none")
    return matches


def _b64(text):
    return encoding.from_base64url(text)


def _open_optional(key, raw_b64, associated_data):
    """Some ciphertexts are stored as an empty string rather than sealed:
    an absent vault description, an entry with no notes. Decrypting "" would
    fail inside the wire decoder before the AEAD ever ran.
    """
    if not raw_b64:
        return ""
    return primitives.aead_open(key, _b64(raw_b64), associated_data).decode("utf-8")


class ReferenceReplayTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corpus = compat.load("v1")
        cls.rows = json.loads(cls.corpus.rows.read_text(encoding="utf-8"))
        cls.identity = _only(cls.rows, "vault.accountidentity")
        # An account is named by its AccountIdentity uuid, never the numeric
        # auth.User id - see workspace/vault/tests/reference/ad.py.
        cls.account_uuid = cls.identity["pk"]

    def _kex_private_key(self):
        identity = self.identity["fields"]
        amk = primitives.derive_amk(
            self.corpus.credentials["vault_master_password"],
            primitives.crockford_decode(self.corpus.credentials["secret_key"]),
            _b64(identity["kdf_salt"]),
            identity["kdf_params"],
        )
        unwrap = primitives.hkdf(amk, ad.unwrap_info())
        kex_priv = primitives.aead_open(
            unwrap,
            _b64(identity["wrapped_kex_priv"]),
            ad.kex_priv_ad(self.account_uuid),
        )
        return X25519PrivateKey.from_private_bytes(kex_priv)

    def test_the_account_private_keys_open_under_the_stored_parameters(self):
        # The one manifest field the rest of this file never touches: every
        # other test reaches the account through the identity row alone.
        self.assertEqual(self.account_uuid, self.corpus.manifest["account_uuid"])
        kex_priv = self._kex_private_key()
        self.assertEqual(len(primitives.private_bytes(kex_priv)), 32)

    def test_every_ciphertext_opens_to_the_manifest(self):
        """Every ciphertext the corpus holds, opened under the key and the
        associated data the write path would have used, compared against the
        manifest's own cleartext - never against a value derived from the
        same bytes being checked.
        """
        kex_priv = self._kex_private_key()
        manifest_vaults = self.corpus.manifest["vaults"]

        vault_rows = {row["pk"]: row["fields"] for row in _all(self.rows, "vault.vault")}
        keywrap_by_vault = {
            row["fields"]["vault"]: row["fields"]
            for row in _all(self.rows, "vault.vaultkeywrap")
        }
        folder_rows = _all(self.rows, "vault.vaultfolder")
        tag_rows = _all(self.rows, "vault.vaulttag")
        entry_rows = _all(self.rows, "vault.vaultentry")
        field_rows = _all(self.rows, "vault.entryfield")

        # Counted against the manifest's own numbers before anything is
        # opened: a loop over an empty list must never be mistaken for a
        # loop that ran and agreed.
        self.assertEqual(len(manifest_vaults), 2)
        self.assertEqual(len(vault_rows), len(manifest_vaults))
        self.assertEqual(len(keywrap_by_vault), len(manifest_vaults))
        expected_folders = sum(len(v["folders"]) for v in manifest_vaults)
        expected_tags = sum(len(v["tags"]) for v in manifest_vaults)
        expected_entries = sum(len(v["entries"]) for v in manifest_vaults)
        expected_fields = sum(
            len(e["fields"]) for v in manifest_vaults for e in v["entries"]
        )
        self.assertEqual(len(folder_rows), expected_folders)
        self.assertEqual(len(tag_rows), expected_tags)
        self.assertEqual(len(entry_rows), expected_entries)
        self.assertEqual(len(field_rows), expected_fields)
        self.assertGreater(expected_folders, 0)
        self.assertGreater(expected_tags, 0)
        self.assertGreater(expected_entries, 0)
        self.assertGreater(expected_fields, 0)

        folder_rows_by_uuid = {row["pk"]: row["fields"] for row in folder_rows}
        tag_rows_by_uuid = {row["pk"]: row["fields"] for row in tag_rows}
        entry_rows_by_uuid = {row["pk"]: row["fields"] for row in entry_rows}
        fields_by_entry = {}
        for row in field_rows:
            fields_by_entry.setdefault(row["fields"]["entry"], {})[
                row["fields"]["field_id"]
            ] = row["fields"]["encrypted_value"]

        opened_vaults = opened_folders = opened_tags = 0
        opened_entries = opened_fields = 0

        for manifest_vault in manifest_vaults:
            vault_uuid = manifest_vault["uuid"]
            vault_row = vault_rows[vault_uuid]
            keywrap_row = keywrap_by_vault[vault_uuid]

            vault_key = primitives.hpke_open(
                kex_priv,
                ad.vault_key_info(vault_uuid, self.account_uuid),
                _b64(keywrap_row["wrapped_key"]),
            )
            self.assertEqual(len(vault_key), 32)
            # Folder and tag names travel under this same key - it is what
            # session.openVaultKey() hands callers, never the raw HPKE-opened
            # vault key.
            vault_meta_key = primitives.hkdf(vault_key, ad.vault_meta_info(vault_uuid))

            self.assertEqual(
                _open_optional(
                    vault_meta_key,
                    vault_row["encrypted_name"],
                    ad.vault_field_ad(vault_uuid, "name"),
                ),
                manifest_vault["name"],
            )
            self.assertEqual(
                _open_optional(
                    vault_meta_key,
                    vault_row["encrypted_description"],
                    ad.vault_field_ad(vault_uuid, "description"),
                ),
                manifest_vault["description"],
            )
            opened_vaults += 1

            for manifest_folder in manifest_vault["folders"]:
                folder_uuid = manifest_folder["uuid"]
                folder_row = folder_rows_by_uuid[folder_uuid]
                self.assertEqual(
                    _open_optional(
                        vault_meta_key,
                        folder_row["encrypted_name"],
                        ad.folder_field_ad(folder_uuid, "name"),
                    ),
                    manifest_folder["name"],
                )
                opened_folders += 1

            for manifest_tag in manifest_vault["tags"]:
                tag_uuid = manifest_tag["uuid"]
                tag_row = tag_rows_by_uuid[tag_uuid]
                self.assertEqual(
                    _open_optional(
                        vault_meta_key,
                        tag_row["encrypted_name"],
                        ad.tag_field_ad(tag_uuid, "name"),
                    ),
                    manifest_tag["name"],
                )
                opened_tags += 1

            for manifest_entry in manifest_vault["entries"]:
                entry_uuid = manifest_entry["uuid"]
                entry_row = entry_rows_by_uuid[entry_uuid]
                entry_key = primitives.hkdf(vault_key, ad.entry_key_info(entry_uuid))

                self.assertEqual(
                    _open_optional(
                        entry_key,
                        entry_row["encrypted_name"],
                        ad.entry_field_ad(entry_uuid, "name"),
                    ),
                    manifest_entry["name"],
                )
                self.assertEqual(
                    _open_optional(
                        entry_key,
                        entry_row["encrypted_notes"],
                        ad.entry_field_ad(entry_uuid, "notes"),
                    ),
                    manifest_entry["notes"],
                )

                entry_fields = fields_by_entry.get(entry_uuid, {})
                self.assertEqual(set(entry_fields), set(manifest_entry["fields"]))
                for field_id, expected_value in manifest_entry["fields"].items():
                    opened = primitives.aead_open(
                        entry_key,
                        _b64(entry_fields[field_id]),
                        ad.entry_field_ad(entry_uuid, ad.qualify_field_id(field_id)),
                    )
                    self.assertEqual(opened.decode("utf-8"), expected_value)
                    opened_fields += 1

                opened_entries += 1

        self.assertEqual(opened_vaults, len(manifest_vaults))
        self.assertEqual(opened_folders, len(folder_rows))
        self.assertEqual(opened_tags, len(tag_rows))
        self.assertEqual(opened_entries, len(entry_rows))
        self.assertEqual(opened_fields, len(field_rows))

    def test_every_signature_verifies(self):
        """Every signed row's metadata_sig, rebuilt from the row's own
        plaintext columns and ciphertext blobs and checked against the
        account's signing key - never against a signature manufactured here.
        """
        sig_public_raw = primitives.decode_public_key(
            _b64(self.identity["fields"]["sig_public"])
        )
        sig_public = Ed25519PublicKey.from_public_bytes(sig_public_raw)

        vault_rows = _all(self.rows, "vault.vault")
        folder_rows = _all(self.rows, "vault.vaultfolder")
        tag_rows = _all(self.rows, "vault.vaulttag")
        entry_rows = _all(self.rows, "vault.vaultentry")
        field_rows = _all(self.rows, "vault.entryfield")

        # Derived from the manifest, like the counts in the ciphertext test -
        # never a literal, so an append-only corpus keeps both in step.
        manifest_vaults = self.corpus.manifest["vaults"]
        self.assertEqual(len(vault_rows), len(manifest_vaults))
        self.assertEqual(
            len(folder_rows), sum(len(v["folders"]) for v in manifest_vaults)
        )
        self.assertEqual(len(tag_rows), sum(len(v["tags"]) for v in manifest_vaults))
        self.assertEqual(
            len(entry_rows), sum(len(v["entries"]) for v in manifest_vaults)
        )
        expected = len(vault_rows) + len(folder_rows) + len(tag_rows) + len(entry_rows)

        fields_by_entry = {}
        for row in field_rows:
            fields_by_entry.setdefault(row["fields"]["entry"], {})[
                row["fields"]["field_id"]
            ] = row["fields"]["encrypted_value"]

        verified = 0

        for row in vault_rows:
            fields = row["fields"]
            payload = metadata.vault_metadata_payload(
                vault_uuid=row["pk"],
                owner_account_uuid=self.account_uuid,
                encrypted_name=fields["encrypted_name"],
                encrypted_description=fields["encrypted_description"],
                icon=fields["icon"],
                color=fields["color"],
                key_version=fields["key_version"],
                is_favorite=fields["is_favorite"],
            )
            primitives.verify(
                sig_public,
                primitives.canonical_cbor(payload),
                _b64(fields["metadata_sig"]),
                expected_type=metadata.VAULT_METADATA_TYPE,
            )
            verified += 1

        for row in folder_rows:
            fields = row["fields"]
            payload = metadata.folder_metadata_payload(
                folder_uuid=row["pk"],
                vault_uuid=fields["vault"],
                signer_account_uuid=self.account_uuid,
                parent_uuid=fields["parent"],
                position=fields["position"],
                encrypted_name=fields["encrypted_name"],
            )
            primitives.verify(
                sig_public,
                primitives.canonical_cbor(payload),
                _b64(fields["metadata_sig"]),
                expected_type=metadata.FOLDER_METADATA_TYPE,
            )
            verified += 1

        for row in tag_rows:
            fields = row["fields"]
            payload = metadata.tag_metadata_payload(
                tag_uuid=row["pk"],
                vault_uuid=fields["vault"],
                signer_account_uuid=self.account_uuid,
                encrypted_name=fields["encrypted_name"],
                color=fields["color"],
            )
            primitives.verify(
                sig_public,
                primitives.canonical_cbor(payload),
                _b64(fields["metadata_sig"]),
                expected_type=metadata.TAG_METADATA_TYPE,
            )
            verified += 1

        for row in entry_rows:
            fields = row["fields"]
            payload = metadata.entry_metadata_payload(
                entry_uuid=row["pk"],
                vault_uuid=fields["vault"],
                signer_account_uuid=self.account_uuid,
                entry_type=fields["type"],
                folder_uuid=fields["folder"],
                encrypted_name=fields["encrypted_name"],
                encrypted_notes=fields["encrypted_notes"],
                key_version=fields["key_version"],
                entry_version=fields["entry_version"],
                is_favorite=fields["is_favorite"],
                tag_uuids=fields["tags"],
                fields=fields_by_entry.get(row["pk"], {}),
            )
            primitives.verify(
                sig_public,
                primitives.canonical_cbor(payload),
                _b64(fields["metadata_sig"]),
                expected_type=metadata.ENTRY_METADATA_TYPE,
            )
            verified += 1

        self.assertEqual(verified, expected)

    def test_deriving_at_the_wrong_parameters_fails_to_open_the_account(self):
        """The corpus happens to have been written at today's Argon2
        defaults (onboarding gives a user no way to choose otherwise), so a
        reader that ignored the stored row and derived at fixed parameters
        would still open it - a coincidence, not a proof that the row is
        read. What actually matters is that a wrong set of parameters fails:
        that only holds if derivation is driven by kdf_params on the row,
        never by a constant in the code deriving it.
        """
        identity = self.identity["fields"]
        wrong_params = {"v": "1.3", "m": 8192, "t": 2, "p": 1}
        amk = primitives.derive_amk(
            self.corpus.credentials["vault_master_password"],
            primitives.crockford_decode(self.corpus.credentials["secret_key"]),
            _b64(identity["kdf_salt"]),
            wrong_params,
        )
        unwrap = primitives.hkdf(amk, ad.unwrap_info())
        with self.assertRaises(InvalidTag):
            primitives.aead_open(
                unwrap,
                _b64(identity["wrapped_kex_priv"]),
                ad.kex_priv_ad(self.account_uuid),
            )

    def test_the_corpus_archive_opens_with_its_passphrase(self):
        tree = archive.open_archive(
            self.corpus.archive, self.corpus.credentials["archive_passphrase"]
        )
        self.assertEqual(tree["format"], "vault-archive")

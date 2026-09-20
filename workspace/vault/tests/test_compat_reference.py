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
from pyhpke import AEADId, KDFId, KEMId
from pyhpke.exceptions import OpenError

from . import compat
from .reference import ad, archive, encoding, metadata, primitives

# The corpora this file's replays actually open, and the versions derived
# from them. test_compat_frozen checks COVERED against compat.versions() so a
# new corpus directory can never go unread - deriving the list from the loads
# themselves is what stops a version being *declared* covered by a replay that
# never reads it.
CORPORA = (compat.load("v1"),)
COVERED = [corpus.root.name for corpus in CORPORA]


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
        (cls.corpus,) = CORPORA
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

        vault_rows = {
            row["pk"]: row["fields"] for row in _all(self.rows, "vault.vault")
        }
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

        This test proves the parameter is *used*, but only in combination with
        test_the_account_private_keys_open_under_the_stored_parameters (which
        proves the stored parameters are *read* from the row) does it fully
        establish that readers respect the parameters written at account
        creation time.
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

    def test_the_frozen_rows_name_the_algorithms_they_were_sealed_under(self):
        """`kdf_algo` sits on the account and `hpke_suite` on every wrap, and
        no reader consults either: one suite is implemented, so each assumes
        it. They are the only columns the replays carry without checking, and
        a corpus whose descriptor disagreed with its own bytes would replay
        green for ever - the first reader to dispatch on the descriptor would
        be the one to find out, against data nobody can regenerate.

        `kdf_params` is deliberately not pinned to a value: it is per-account
        by design, and the two tests above already prove a reader obeys what
        the row carries.
        """
        self.assertEqual(self.identity["fields"]["kdf_algo"], "argon2id")
        wraps = _all(self.rows, "vault.vaultkeywrap")
        self.assertEqual(len(wraps), len(self.corpus.manifest["vaults"]))
        for wrap in wraps:
            self.assertEqual(wrap["fields"]["hpke_suite"], primitives.HPKE_SUITE_V1)

    def test_a_wrapped_vault_key_refuses_a_suite_it_was_not_sealed_under(self):
        """The other half: a descriptor is worth freezing only if it names
        something load-bearing. AES-128-GCM is one number away from the
        stored aead_id and the identifier feeds the HPKE key schedule, so a
        wrap sealed under v1 cannot open under it.
        """
        kex_priv = self._kex_private_key()
        wrap = _all(self.rows, "vault.vaultkeywrap")[0]["fields"]
        sealed = _b64(wrap["wrapped_key"])
        suite = primitives.hpke_suite(
            KEMId.DHKEM_X25519_HKDF_SHA256, KDFId.HKDF_SHA256, AEADId.AES128_GCM
        )
        # The split hpke_open makes, at the encapsulated key size DHKEM
        # (X25519) fixes: the encapsulated key, then the ciphertext.
        recipient = suite.create_recipient_context(
            enc=sealed[:32],
            skr=suite.kem.deserialize_private_key(primitives.private_bytes(kex_priv)),
            info=ad.vault_key_info(wrap["vault"], self.account_uuid),
        )
        with self.assertRaises(OpenError):
            recipient.open(sealed[32:], aad=b"")

    def test_the_corpus_archive_opens_to_the_whole_account(self):
        """The archive decodes to the entire account in the clear, so every
        part of it is compared to the manifest - the cleartext the browser
        reported at generation time, never a value taken from the archive
        itself.

        This replay carries more weight than its siblings: the archive is
        read by one implementation and one only. The bundle exports archives
        but cannot open them (vault_archive.js has no reader), and the server
        never sees one. Nothing else in this module cross-checks these bytes,
        so an assertion that stopped at the container's format string would
        leave a decoder free to mangle every string it returns.
        """
        tree = archive.open_archive(
            self.corpus.archive, self.corpus.credentials["archive_passphrase"]
        )
        self.assertEqual(tree["format"], "vault-archive")
        self.assertEqual(tree["version"], 1)

        # Counted from the manifest before anything is compared, like the
        # ciphertext replay above: a walk over an empty archive must never
        # pass for a walk that ran and agreed.
        manifest_vaults = self.corpus.manifest["vaults"]
        expected_vaults = len(manifest_vaults)
        expected_folders = sum(len(v["folders"]) for v in manifest_vaults)
        expected_tags = sum(len(v["tags"]) for v in manifest_vaults)
        expected_entries = sum(len(v["entries"]) for v in manifest_vaults)
        expected_fields = sum(
            len(e["fields"]) for v in manifest_vaults for e in v["entries"]
        )
        self.assertGreater(expected_vaults, 0)
        self.assertGreater(expected_folders, 0)
        self.assertGreater(expected_tags, 0)
        self.assertGreater(expected_entries, 0)
        self.assertGreater(expected_fields, 0)

        # The two structures name the same things differently: the manifest
        # points at rows by UUID, the archive - which has no rows to point at
        # - numbers its folders and tags within the vault and refers to those
        # numbers. The name is the only key both carry, so both sides are
        # keyed by it and the numbering is resolved back to names before
        # anything is compared.
        archive_vaults = {vault["name"]: vault for vault in tree["vaults"]}
        self.assertEqual(len(archive_vaults), len(tree["vaults"]))
        self.assertEqual(
            sorted(archive_vaults), sorted(v["name"] for v in manifest_vaults)
        )

        compared_vaults = compared_folders = compared_tags = 0
        compared_entries = compared_fields = 0

        for manifest_vault in manifest_vaults:
            archive_vault = archive_vaults[manifest_vault["name"]]
            self.assertEqual(
                archive_vault["description"], manifest_vault["description"]
            )
            self.assertEqual(archive_vault["icon"], manifest_vault["icon"])
            self.assertEqual(archive_vault["color"], manifest_vault["color"])
            self.assertEqual(
                archive_vault["is_favorite"], manifest_vault["is_favorite"]
            )
            compared_vaults += 1

            folder_name_by_id = {f["id"]: f["name"] for f in archive_vault["folders"]}
            self.assertEqual(len(folder_name_by_id), len(archive_vault["folders"]))
            folder_name_by_uuid = {
                f["uuid"]: f["name"] for f in manifest_vault["folders"]
            }
            archive_folders = {f["name"]: f for f in archive_vault["folders"]}
            self.assertEqual(
                sorted(archive_folders), sorted(folder_name_by_uuid.values())
            )
            for manifest_folder in manifest_vault["folders"]:
                archive_folder = archive_folders[manifest_folder["name"]]
                self.assertEqual(
                    archive_folder["position"], manifest_folder["position"]
                )
                # Resolved to names on both sides: "Cards sits in Banking" is
                # the claim, and comparing a 0 to a UUID could never make it.
                # `is not None` on the archive side, never truthiness - the
                # first folder of a vault is numbered 0.
                expected_parent = (
                    folder_name_by_uuid[manifest_folder["parent"]]
                    if manifest_folder["parent"] is not None
                    else None
                )
                actual_parent = (
                    folder_name_by_id[archive_folder["parent"]]
                    if archive_folder["parent"] is not None
                    else None
                )
                self.assertEqual(actual_parent, expected_parent)
                compared_folders += 1

            tag_name_by_id = {t["id"]: t["name"] for t in archive_vault["tags"]}
            self.assertEqual(len(tag_name_by_id), len(archive_vault["tags"]))
            tag_name_by_uuid = {t["uuid"]: t["name"] for t in manifest_vault["tags"]}
            archive_tags = {t["name"]: t for t in archive_vault["tags"]}
            self.assertEqual(sorted(archive_tags), sorted(tag_name_by_uuid.values()))
            for manifest_tag in manifest_vault["tags"]:
                self.assertEqual(
                    archive_tags[manifest_tag["name"]]["color"], manifest_tag["color"]
                )
                compared_tags += 1

            archive_entries = {e["name"]: e for e in archive_vault["entries"]}
            self.assertEqual(len(archive_entries), len(archive_vault["entries"]))
            self.assertEqual(
                sorted(archive_entries),
                sorted(e["name"] for e in manifest_vault["entries"]),
            )
            for manifest_entry in manifest_vault["entries"]:
                archive_entry = archive_entries[manifest_entry["name"]]
                self.assertEqual(archive_entry["type"], manifest_entry["type"])
                # The notes are the corpus's only NFD string, so this is the
                # line a decoder that normalised what it decoded would break.
                self.assertEqual(archive_entry["notes"], manifest_entry["notes"])
                self.assertEqual(
                    archive_entry["favorite"], manifest_entry["is_favorite"]
                )
                self.assertEqual(archive_entry["trashed"], manifest_entry["trashed"])
                expected_folder = (
                    folder_name_by_uuid[manifest_entry["folder"]]
                    if manifest_entry["folder"] is not None
                    else None
                )
                actual_folder = (
                    folder_name_by_id[archive_entry["folder"]]
                    if archive_entry["folder"] is not None
                    else None
                )
                self.assertEqual(actual_folder, expected_folder)
                self.assertEqual(
                    sorted(tag_name_by_id[tag_id] for tag_id in archive_entry["tags"]),
                    sorted(
                        tag_name_by_uuid[tag_uuid]
                        for tag_uuid in manifest_entry["tags"]
                    ),
                )

                self.assertEqual(
                    set(archive_entry["fields"]), set(manifest_entry["fields"])
                )
                for field_id, expected_value in manifest_entry["fields"].items():
                    self.assertEqual(archive_entry["fields"][field_id], expected_value)
                    compared_fields += 1
                compared_entries += 1

        self.assertEqual(compared_vaults, expected_vaults)
        self.assertEqual(compared_folders, expected_folders)
        self.assertEqual(compared_tags, expected_tags)
        self.assertEqual(compared_entries, expected_entries)
        self.assertEqual(compared_fields, expected_fields)

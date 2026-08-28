"""Server-side verification of a vault's metadata signature.

The bytes are not invented here: the frozen vector is the same one the browser
bundle reproduces, so a divergence between the two implementations fails in
this file rather than in a user's vault.
"""

import base64
import json
import pathlib

from django.test import SimpleTestCase

from workspace.vault.services.attestation import PUBKEY_ALG_ED25519, AttestationError
from workspace.vault.services.metadata import (
    canonical_cbor,
    entry_metadata_payload,
    folder_metadata_payload,
    tag_metadata_payload,
    vault_metadata_payload,
    verify_record,
    verify_vault_metadata,
)

VECTORS = json.loads(
    (pathlib.Path(__file__).resolve().parent / "crypto_vectors.json").read_text(
        encoding="utf-8"
    )
)


DECOMPOSED = "cafe\u0301"
PRECOMPOSED = "caf\u00e9"


def _vector(kind, name):
    return next(item for item in VECTORS[kind] if item["id"] == name)


class VaultMetadataTests(SimpleTestCase):
    def setUp(self):
        self.frozen = _vector("cbor", "vault-metadata")
        self.signature = _vector("ed25519", "vault-metadata-signature")
        self.payload = vault_metadata_payload(**_signed_fields(self.frozen["payload"]))
        self.sig_public = _prefixed_public_key(self.signature["pk_b64"])

    def test_the_payload_encodes_to_the_frozen_bytes(self):
        self.assertEqual(
            _to_base64url(canonical_cbor(self.payload)), self.frozen["expected_b64"]
        )

    def test_the_frozen_signature_verifies(self):
        verify_vault_metadata(
            self.payload, self.sig_public, self.signature["expected_sig_b64"]
        )

    def test_a_renamed_vault_no_longer_verifies(self):
        """The whole point: the server re-encodes what it is about to store,
        so a name the signature never covered is refused."""
        tampered = dict(self.payload, encrypted_name="AQEBAAABdGFtcGVyZWQ")
        with self.assertRaises(AttestationError):
            verify_vault_metadata(
                tampered, self.sig_public, self.signature["expected_sig_b64"]
            )

    def test_a_flipped_favourite_no_longer_verifies(self):
        tampered = dict(self.payload, is_favorite=True)
        with self.assertRaises(AttestationError):
            verify_vault_metadata(
                tampered, self.sig_public, self.signature["expected_sig_b64"]
            )

    def test_another_accounts_signing_key_is_refused(self):
        other = _prefixed_public_key(
            _vector("hpke", "vault-key-self-wrap")["recipient_pk_b64"]
        )
        with self.assertRaises(AttestationError):
            verify_vault_metadata(
                self.payload, other, self.signature["expected_sig_b64"]
            )

    def test_a_signature_with_an_unknown_algorithm_byte_is_refused(self):
        raw = bytearray(_from_base64url(self.signature["expected_sig_b64"]))
        raw[0] = 0x7F
        with self.assertRaises(AttestationError):
            verify_vault_metadata(
                self.payload, self.sig_public, _to_base64url(bytes(raw))
            )

    def test_a_truncated_signature_is_refused(self):
        raw = _from_base64url(self.signature["expected_sig_b64"])[:-1]
        with self.assertRaises(AttestationError):
            verify_vault_metadata(self.payload, self.sig_public, _to_base64url(raw))

    def test_a_float_in_the_payload_is_refused_rather_than_encoded(self):
        """Canonical CBOR admits no floats, and cbor2 would happily write one:
        a payload the browser cannot reproduce must fail here, loudly."""
        with self.assertRaises(AttestationError):
            canonical_cbor(dict(self.payload, key_version=1.5))


def _signed_fields(payload):
    return {key: value for key, value in payload.items() if key not in ("v", "type")}


def _to_base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _from_base64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _prefixed_public_key(raw_b64: str) -> str:
    return _to_base64url(bytes([PUBKEY_ALG_ED25519]) + _from_base64url(raw_b64))


def _record_vector(name):
    """The frozen payload of a record type, paired with its frozen signature."""
    signature = _vector("ed25519", f"{name}-signature")
    return {
        "payload": _vector("cbor", name)["payload"],
        "expected_b64": _vector("cbor", name)["expected_b64"],
        # Already algorithm-prefixed by the generator, like every signature
        # that travels on the wire.
        "signature": signature["expected_sig_b64"],
        "sig_public": _prefixed_public_key(signature["pk_b64"]),
    }


class CanonicalCborRecursionTests(SimpleTestCase):
    def test_canonical_cbor_normalises_nested_strings(self):
        """The browser's encoder recurses; this one must, or a combining
        accent inside a field value signs different bytes on each side."""
        decomposed = DECOMPOSED
        precomposed = PRECOMPOSED
        self.assertNotEqual(decomposed, precomposed)
        self.assertEqual(
            canonical_cbor({"fields": [["custom:x", decomposed]]}),
            canonical_cbor({"fields": [["custom:x", precomposed]]}),
        )

    def test_canonical_cbor_accepts_a_list_payload_member(self):
        encoded = canonical_cbor({"tags": ["a", "b"], "fields": []})
        self.assertIsInstance(encoded, bytes)

    def test_canonical_cbor_still_refuses_a_float_anywhere(self):
        with self.assertRaises(AttestationError):
            canonical_cbor({"fields": [["custom:x", 1.5]]})


class RecordSignatureTests(SimpleTestCase):
    def test_the_three_builders_reproduce_the_frozen_bytes(self):
        """The server builds its own payloads; a divergence from the reference
        in key order, lowercasing or sort order would surface as a signature
        that stops verifying, and nothing else would report it."""
        entry = _record_vector("entry-metadata")
        self.assertEqual(
            canonical_cbor(
                entry_metadata_payload(
                    entry_uuid=entry["payload"]["entry_uuid"].upper(),
                    vault_uuid=entry["payload"]["vault_uuid"],
                    signer_account_uuid=entry["payload"]["signer_account_uuid"],
                    entry_type=entry["payload"]["entry_type"],
                    folder_uuid=entry["payload"]["folder_uuid"],
                    encrypted_name=entry["payload"]["encrypted_name"],
                    encrypted_notes=entry["payload"]["encrypted_notes"],
                    key_version=entry["payload"]["key_version"],
                    entry_version=entry["payload"]["entry_version"],
                    is_favorite=entry["payload"]["is_favorite"],
                    # Reversed on purpose: the builder owns the sort.
                    tag_uuids=list(reversed(entry["payload"]["tags"])),
                    fields={
                        key: value
                        for key, value in reversed(entry["payload"]["fields"])
                    },
                )
            ),
            _from_base64url(entry["expected_b64"]),
        )

        folder = _record_vector("folder-metadata")
        self.assertEqual(
            canonical_cbor(
                folder_metadata_payload(
                    folder_uuid=folder["payload"]["folder_uuid"],
                    vault_uuid=folder["payload"]["vault_uuid"],
                    signer_account_uuid=folder["payload"]["signer_account_uuid"],
                    parent_uuid=folder["payload"]["parent_uuid"],
                    position=folder["payload"]["position"],
                    encrypted_name=folder["payload"]["encrypted_name"],
                )
            ),
            _from_base64url(folder["expected_b64"]),
        )

        tag = _record_vector("tag-metadata")
        self.assertEqual(
            canonical_cbor(
                tag_metadata_payload(
                    tag_uuid=tag["payload"]["tag_uuid"],
                    vault_uuid=tag["payload"]["vault_uuid"],
                    signer_account_uuid=tag["payload"]["signer_account_uuid"],
                    encrypted_name=tag["payload"]["encrypted_name"],
                    color=tag["payload"]["color"],
                )
            ),
            _from_base64url(tag["expected_b64"]),
        )

    def test_entry_signature_verifies_against_the_reference_vector(self):
        vector = _record_vector("entry-metadata")
        verify_record(vector["payload"], vector["sig_public"], vector["signature"])

    def test_an_entry_signature_over_a_different_field_set_is_refused(self):
        vector = _record_vector("entry-metadata")
        tampered = dict(vector["payload"])
        tampered["fields"] = [
            pair for pair in tampered["fields"] if pair[0] != "password"
        ]
        self.assertNotEqual(tampered["fields"], vector["payload"]["fields"])
        with self.assertRaises(AttestationError):
            verify_record(tampered, vector["sig_public"], vector["signature"])

    def test_an_entry_signature_over_a_replayed_field_value_is_refused(self):
        """Associated data cannot see a ciphertext replayed into its own slot;
        the field set inside the signature is what does."""
        vector = _record_vector("entry-metadata")
        tampered = dict(vector["payload"])
        tampered["fields"] = [
            [key, "Ag" if key == "password" else value]
            for key, value in vector["payload"]["fields"]
        ]
        with self.assertRaises(AttestationError):
            verify_record(tampered, vector["sig_public"], vector["signature"])

    def test_an_entry_signature_over_a_different_tag_set_is_refused(self):
        vector = _record_vector("entry-metadata")
        tampered = dict(vector["payload"])
        self.assertTrue(tampered["tags"])
        tampered["tags"] = []
        with self.assertRaises(AttestationError):
            verify_record(tampered, vector["sig_public"], vector["signature"])

    def test_a_folder_signature_verifies_and_a_different_parent_is_refused(self):
        vector = _record_vector("folder-metadata")
        verify_record(vector["payload"], vector["sig_public"], vector["signature"])
        tampered = dict(vector["payload"])
        tampered["parent_uuid"] = "018f3f6e-0000-7000-8000-0000000000ee"
        with self.assertRaises(AttestationError):
            verify_record(tampered, vector["sig_public"], vector["signature"])

    def test_a_folder_signature_over_a_different_position_is_refused(self):
        vector = _record_vector("folder-metadata")
        tampered = dict(vector["payload"], position=vector["payload"]["position"] + 1)
        with self.assertRaises(AttestationError):
            verify_record(tampered, vector["sig_public"], vector["signature"])

    def test_a_tag_signature_verifies_and_a_recoloured_tag_is_refused(self):
        vector = _record_vector("tag-metadata")
        verify_record(vector["payload"], vector["sig_public"], vector["signature"])
        tampered = dict(vector["payload"], color="accent")
        with self.assertRaises(AttestationError):
            verify_record(tampered, vector["sig_public"], vector["signature"])

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
    vault_metadata_payload,
    verify_vault_metadata,
)

VECTORS = json.loads(
    (pathlib.Path(__file__).resolve().parent / "crypto_vectors.json").read_text(
        encoding="utf-8"
    )
)


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

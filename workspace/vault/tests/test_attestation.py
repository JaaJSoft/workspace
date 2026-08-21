import json
import pathlib

from django.test import SimpleTestCase

from workspace.vault.services.attestation import (
    PUBKEY_ALG_ED25519,
    PUBKEY_ALG_X25519,
    AttestationError,
    decode_base64url,
    decode_public_key,
    verify_kex_pub_attestation,
)
from workspace.vault.tests.reference import ad, primitives
from workspace.vault.tests.reference.encoding import from_base64url, to_base64url
from workspace.vault.tests.reference.generate_vectors import VECTORS_PATH

ACCOUNT_UUID = "0192f3a4-1111-7d8e-9f01-23456789abcd"
OTHER_UUID = "0192f3a4-2222-7d8e-9f01-23456789abcd"
VECTORS = json.loads(pathlib.Path(VECTORS_PATH).read_text(encoding="utf-8"))


def build_attestation(account_uuid=ACCOUNT_UUID):
    """The three fields the browser submits, built the way it builds them."""
    kex = primitives.generate_kex_keypair()
    sig = primitives.generate_sig_keypair()
    kex_public = to_base64url(
        primitives.encode_public_key(kex.public_key(), primitives.PUBKEY_ALG_X25519)
    )
    sig_public = to_base64url(
        primitives.encode_public_key(sig.public_key(), primitives.PUBKEY_ALG_ED25519)
    )
    signature = to_base64url(
        primitives.sign_bytes(sig, ad.kex_pub_payload(account_uuid, kex_public))
    )
    return kex_public, sig_public, signature


class VerifyKexPubAttestationTests(SimpleTestCase):
    """Verification signals by raising and returns nothing, so the tests that
    expect acceptance simply call it: an unraised exception is the assertion.
    Only the refusals need one."""

    def test_accepts_a_well_formed_attestation(self):
        kex_pub, sig_pub, signature = build_attestation()
        verify_kex_pub_attestation(ACCOUNT_UUID, kex_pub, sig_pub, signature)

    def test_accepts_an_uppercase_account_uuid(self):
        """The catalogue lowercases the UUID before signing, so a caller that
        hands over the canonical uppercase spelling must still verify."""
        kex_pub, sig_pub, signature = build_attestation()
        verify_kex_pub_attestation(ACCOUNT_UUID.upper(), kex_pub, sig_pub, signature)

    def test_refuses_an_attestation_bound_to_another_account(self):
        kex_pub, sig_pub, signature = build_attestation()
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(OTHER_UUID, kex_pub, sig_pub, signature)

    def test_refuses_an_attestation_over_another_key(self):
        kex_pub, sig_pub, signature = build_attestation()
        other_kex_pub, _, _ = build_attestation()
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, other_kex_pub, sig_pub, signature)

    def test_refuses_a_signature_from_another_identity(self):
        kex_pub, sig_pub, _ = build_attestation()
        _, _, other_signature = build_attestation()
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, kex_pub, sig_pub, other_signature)

    def test_refuses_a_signature_algorithm_it_does_not_know(self):
        kex_pub, sig_pub, signature = build_attestation()
        raw = bytearray(from_base64url(signature))
        raw[0] = 0x7F
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(
                ACCOUNT_UUID, kex_pub, sig_pub, to_base64url(bytes(raw))
            )

    def test_refuses_a_truncated_signature(self):
        kex_pub, sig_pub, signature = build_attestation()
        truncated = to_base64url(from_base64url(signature)[:-1])
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, kex_pub, sig_pub, truncated)

    def test_refuses_a_sig_public_stored_under_the_kex_algorithm_byte(self):
        """A 32-byte Ed25519 key labelled 0x01 is an X25519 key as far as the
        catalogue is concerned. Accepting it would make the label decorative,
        and the label is signed."""
        kex_pub, _, signature = build_attestation()
        sig = primitives.generate_sig_keypair()
        mislabelled = to_base64url(
            bytes([primitives.PUBKEY_ALG_X25519])
            + primitives.public_bytes(sig.public_key())
        )
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, kex_pub, mislabelled, signature)

    def test_refuses_a_kex_public_stored_under_the_sig_algorithm_byte(self):
        _, sig_pub, signature = build_attestation()
        kex = primitives.generate_kex_keypair()
        mislabelled = to_base64url(
            bytes([primitives.PUBKEY_ALG_ED25519])
            + primitives.public_bytes(kex.public_key())
        )
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, mislabelled, sig_pub, signature)

    def test_refuses_malformed_base64url(self):
        kex_pub, sig_pub, _ = build_attestation()
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, kex_pub, sig_pub, "!!!not base64")

    def test_refuses_base64url_that_decodes_to_no_bytes(self):
        """`urlsafe_b64decode` discards characters outside the alphabet, so
        "====" and "!!!!" both decode to b"" - and an empty signature reaches
        the algorithm-byte check as an index into nothing."""
        kex_pub, sig_pub, _ = build_attestation()
        for text in ("====", "!!!!"):
            with self.subTest(text=text):
                with self.assertRaises(AttestationError):
                    verify_kex_pub_attestation(ACCOUNT_UUID, kex_pub, sig_pub, text)

    def test_refuses_an_empty_field(self):
        kex_pub, sig_pub, signature = build_attestation()
        for kex, sig, sign in (
            ("", sig_pub, signature),
            (kex_pub, "", signature),
            (kex_pub, sig_pub, ""),
        ):
            with self.subTest(missing=(not kex, not sig, not sign)):
                with self.assertRaises(AttestationError):
                    verify_kex_pub_attestation(ACCOUNT_UUID, kex, sig, sign)

    def test_refuses_a_non_string_field(self):
        kex_pub, sig_pub, signature = build_attestation()
        with self.assertRaises(AttestationError):
            verify_kex_pub_attestation(ACCOUNT_UUID, None, sig_pub, signature)


class DecodeHelperTests(SimpleTestCase):
    """The two decoders are exported for the PRs that will verify vault and
    entry metadata signatures, so their refusals are tested here rather than
    only through the one caller that exists today."""

    def test_refuses_a_public_key_of_the_wrong_length(self):
        with self.assertRaises(AttestationError):
            decode_public_key(
                bytes([PUBKEY_ALG_ED25519]) + bytes(31), PUBKEY_ALG_ED25519
            )

    def test_refuses_an_empty_public_key(self):
        with self.assertRaises(AttestationError):
            decode_public_key(b"", PUBKEY_ALG_ED25519)

    def test_refuses_a_public_key_under_another_algorithm(self):
        with self.assertRaises(AttestationError):
            decode_public_key(
                bytes([PUBKEY_ALG_X25519]) + bytes(32), PUBKEY_ALG_ED25519
            )

    def test_accepts_unpadded_and_padded_base64url(self):
        self.assertEqual(decode_base64url("AAAA"), bytes(3))

    def test_refuses_characters_outside_the_alphabet(self):
        """Silently discarding them is the default, and it turns junk into a
        short buffer instead of a refusal."""
        with self.assertRaises(AttestationError):
            decode_base64url("AA!!AA")

    def test_refuses_a_value_that_is_not_text(self):
        for value in (None, 42, b"bytes"):
            with self.subTest(value=value):
                with self.assertRaises(AttestationError):
                    decode_base64url(value)


class FrozenVectorTests(SimpleTestCase):
    """The server rebuilds the signed payload rather than importing the parity
    oracle, so only a frozen vector can prove the two still agree."""

    def _attestation_vector(self):
        for entry in VECTORS["ed25519"]:
            if entry["id"] == "account-kex-pub-attestation":
                return entry
        raise AssertionError("the attestation vector is missing")

    def _sig_public(self, vector):
        # The vector publishes the signing key raw; the server wants it stored.
        return to_base64url(
            bytes([primitives.PUBKEY_ALG_ED25519]) + from_base64url(vector["pk_b64"])
        )

    def test_accepts_the_frozen_attestation(self):
        vector = self._attestation_vector()
        verify_kex_pub_attestation(
            vector["account_uuid"],
            vector["kex_public_b64"],
            self._sig_public(vector),
            vector["expected_sig_b64"],
        )

    def test_rebuilds_the_frozen_payload_byte_for_byte(self):
        from workspace.vault.services.attestation import kex_pub_payload

        vector = self._attestation_vector()
        self.assertEqual(
            to_base64url(
                kex_pub_payload(vector["account_uuid"], vector["kex_public_b64"])
            ),
            vector["message_b64"],
        )

import json
import pathlib
import re

from django.conf import settings
from django.test import SimpleTestCase

from workspace.vault.tests.reference import ad, primitives
from workspace.vault.tests.reference.encoding import from_base64url, to_base64url
from workspace.vault.tests.reference.generate_fuzz_corpus import (
    CORPUS_PATH,
    build_corpus,
)
from workspace.vault.tests.reference.generate_vectors import VECTORS_PATH, build_vectors

VECTORS = json.loads(pathlib.Path(VECTORS_PATH).read_text(encoding="utf-8"))


class VectorFileTests(SimpleTestCase):
    def test_the_committed_file_matches_a_fresh_generation(self):
        """The vectors are the contract between the browser and this reference.
        If regenerating them changes a byte, either an input stopped being
        pinned or a primitive changed - both are breaking, and neither should
        be discovered from a user who cannot open their vault.
        """
        self.assertEqual(build_vectors(), VECTORS)

    def test_every_section_carries_at_least_one_vector(self):
        for section in (
            "argon2id",
            "hkdf",
            "aead",
            "hpke",
            "cbor",
            "ed25519",
            "public_keys",
        ):
            self.assertTrue(VECTORS[section], f"section {section} is empty")


class AccountWrapHeaderTests(SimpleTestCase):
    """The header the onboarding writes, held against the frozen vector.

    ``account-kex-priv-wrap`` is the format's word on how the two ciphertexts
    that gate every vault an account owns are labelled. Nothing at runtime
    reads those bytes - ``open`` takes the iv and the ciphertext and ignores
    the rest - so a writer that disagrees with the vector breaks nothing today
    and everything the day an agility step, a second AEAD or an independent
    re-implementation starts trusting them.

    It has to be checked from here rather than from JavaScript: a test on the
    onboarding alone could only compare one literal to another, which is
    precisely how the two spent months disagreeing in silence.
    """

    ONBOARDING = (
        pathlib.Path(settings.BASE_DIR)
        / "workspace/vault/ui/static/vault/ui/js/onboarding.js"
    )
    WIRE = pathlib.Path(settings.BASE_DIR) / "scripts/frontend/src/vault/wire.js"

    def _sealed_literal(self):
        source = self.ONBOARDING.read_text(encoding="utf-8")
        block = re.search(
            r"const sealed = \{\s*keyVersion:\s*(\d+),\s*kdfId:\s*V\.(\w+),?\s*\};",
            source,
        )
        self.assertIsNotNone(
            block, "the onboarding must seal the account wraps from one literal"
        )
        return int(block.group(1)), block.group(2)

    def _wire_constant(self, name):
        source = self.WIRE.read_text(encoding="utf-8")
        declared = re.search(rf"export const {name} = (0x[0-9a-fA-F]+);", source)
        self.assertIsNotNone(declared, f"{name} is not exported by the wire module")
        return int(declared.group(1), 16)

    def _vector(self):
        for entry in VECTORS["aead"]:
            if entry["id"] == "account-kex-priv-wrap":
                return entry
        self.fail("the account wrap vector is gone")

    def test_the_onboarding_seals_the_wraps_as_the_vector_labels_them(self):
        key_version, kdf_name = self._sealed_literal()
        vector = self._vector()
        self.assertEqual(key_version, vector["key_version"])
        self.assertEqual(self._wire_constant(kdf_name), vector["kdf_id"])


class VectorReplayTests(SimpleTestCase):
    def test_argon2id_vectors_replay(self):
        for vector in VECTORS["argon2id"]:
            with self.subTest(vector["id"]):
                self.assertEqual(
                    to_base64url(
                        primitives.derive_amk(
                            vector["password"],
                            from_base64url(vector["secret_key_b64"]),
                            from_base64url(vector["salt_b64"]),
                            vector["params"],
                        )
                    ),
                    vector["expected_amk_b64"],
                )

    def test_aead_vectors_replay_and_open(self):
        for vector in VECTORS["aead"]:
            with self.subTest(vector["id"]):
                raw = primitives.aead_seal(
                    from_base64url(vector["key_b64"]),
                    vector["plaintext"].encode(),
                    vector["ad"].encode(),
                    iv=from_base64url(vector["iv_b64"]),
                    key_version=vector["key_version"],
                    kdf_id=vector["kdf_id"],
                )
                self.assertEqual(to_base64url(raw), vector["expected_wire_b64"])
                self.assertEqual(
                    primitives.aead_open(
                        from_base64url(vector["key_b64"]), raw, vector["ad"].encode()
                    ),
                    vector["plaintext"].encode(),
                )

    def test_hpke_vectors_replay(self):
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        for vector in VECTORS["hpke"]:
            with self.subTest(vector["id"]):
                sender = X25519PrivateKey.from_private_bytes(
                    from_base64url(vector["sender_sk_b64"])
                )
                recipient = X25519PrivateKey.from_private_bytes(
                    from_base64url(vector["recipient_sk_b64"])
                )
                sealed = primitives.hpke_seal(
                    recipient.public_key(),
                    vector["info"].encode(),
                    from_base64url(vector["plaintext_b64"]),
                    sender_private=sender,
                )
                self.assertEqual(to_base64url(sealed), vector["expected_sealed_b64"])

    def test_cbor_and_signature_vectors_replay(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        for vector in VECTORS["cbor"]:
            with self.subTest(vector["id"]):
                self.assertEqual(
                    to_base64url(primitives.canonical_cbor(vector["payload"])),
                    vector["expected_b64"],
                )
        for vector in VECTORS["ed25519"]:
            with self.subTest(vector["id"]):
                signer = Ed25519PrivateKey.from_private_bytes(
                    from_base64url(vector["sk_b64"])
                )
                # A vector carries either a CBOR payload or a raw message; the
                # two signing paths are different entry points and both ship.
                if "message_b64" in vector:
                    # Rebuilt, not replayed: a divergence on what goes into
                    # the attestation has to fail here.
                    self.assertEqual(
                        to_base64url(
                            ad.kex_pub_payload(
                                vector["account_uuid"], vector["kex_public_b64"]
                            )
                        ),
                        vector["message_b64"],
                    )
                    signature = primitives.sign_bytes(
                        signer, from_base64url(vector["message_b64"])
                    )
                else:
                    signature = primitives.sign(signer, vector["payload"])
                self.assertEqual(to_base64url(signature), vector["expected_sig_b64"])

    def test_public_key_vectors_replay(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

        loaders = {
            primitives.PUBKEY_ALG_X25519: X25519PublicKey.from_public_bytes,
            primitives.PUBKEY_ALG_ED25519: Ed25519PublicKey.from_public_bytes,
        }
        for vector in VECTORS["public_keys"]:
            with self.subTest(vector["id"]):
                key = loaders[vector["alg"]](from_base64url(vector["raw_b64"]))
                stored = primitives.encode_public_key(key, vector["alg"])
                self.assertEqual(to_base64url(stored), vector["expected_stored_b64"])
                self.assertEqual(
                    to_base64url(primitives.decode_public_key(stored)),
                    vector["raw_b64"],
                )


class FuzzCorpusTests(SimpleTestCase):
    """The differential corpus the browser suite replays.

    Its value is that it was generated rather than written: both encoding bugs
    this module has had lived in shapes no one thought to hand-pick. Exploring
    new ground means a new seed, not a new assertion.
    """

    def test_the_committed_corpus_matches_a_fresh_generation(self):
        committed = json.loads(pathlib.Path(CORPUS_PATH).read_text(encoding="utf-8"))
        self.assertEqual(build_corpus(committed["seed"], committed["count"]), committed)


class UnencodableInputTests(SimpleTestCase):
    def test_a_negative_integer_the_browser_cannot_match_is_refused(self):
        """Canonically a four-byte negative argument, which cbor-x cannot emit -
        its BigInt path always writes eight. Encoding it anyway would have the
        two implementations sign different bytes for the same number.
        """
        for value in (-(2**31) - 1, -(2**32)):
            with self.subTest(value):
                with self.assertRaises(ValueError):
                    primitives.canonical_cbor({"v": 1, "n": value})

    def test_the_edges_just_outside_that_band_still_encode(self):
        for value in (-(2**31), -(2**32) - 1):
            with self.subTest(value):
                primitives.canonical_cbor({"v": 1, "n": value})

    def test_keys_that_collide_after_normalisation_are_refused(self):
        """One key once normalised, two values: each implementation would keep
        a different one, so neither keeps either.
        """
        with self.assertRaises(ValueError):
            primitives.canonical_cbor({"café": 1, "café": 2})

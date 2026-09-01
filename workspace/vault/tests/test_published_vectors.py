"""The reference implementation against vectors published in the standards.

crypto_vectors.json proves the browser and this reference agree. It cannot
prove they are *right*: one was written to reproduce the other, so a misreading
of a construction would be reproduced identically on both sides and every
parity test would still pass. These vectors come from outside the project and
are the only thing anchoring the chain to something neither implementation had
a say in.

Sources, all fetched from the RFC editor:

- Argon2id: RFC 9106 section 5.3
- HKDF-SHA256: RFC 5869 appendix A, test cases 1 and 3
- Ed25519: RFC 8032 section 7.1, TEST 1 and TEST 2
- HPKE: RFC 9180 appendix A.1, base mode

AES-256-GCM has no vector here: RFC 9180 publishes no X25519 + AES-256-GCM
suite, and the AEAD is reached through a single library call with no framing of
our own beyond the header that test_reference_crypto covers. The HPKE vector
below exercises the same library's AES-128-GCM path.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import SimpleTestCase
from pyhpke import AEADId, KDFId, KEMId
from pyhpke.kem_key import KEMKeyPair

from workspace.vault.tests.reference import primitives, totp


class Argon2idPublishedVectorTests(SimpleTestCase):
    """RFC 9106 section 5.3.

    This is the vector that matters most here. It is the only published one
    with a non-empty `secret`, so it is the only external proof that the K
    parameter this key hierarchy depends on actually reaches the algorithm
    rather than being dropped on the floor.
    """

    def test_the_rfc_9106_tag_is_reproduced(self):
        tag = primitives.argon2id_raw(
            password=bytes([0x01]) * 32,
            salt=bytes([0x02]) * 16,
            secret=bytes([0x03]) * 8,
            associated_data=bytes([0x04]) * 12,
            t=3,
            m=32,
            p=4,
            tag_length=32,
        )
        self.assertEqual(
            tag.hex(),
            "0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659",
        )

    def test_dropping_the_secret_changes_the_tag(self):
        """Guards the guard: if `secret` were being ignored, the vector above
        could only pass by coincidence, and this assertion would fail.
        """
        without_secret = primitives.argon2id_raw(
            password=bytes([0x01]) * 32,
            salt=bytes([0x02]) * 16,
            secret=b"",
            associated_data=bytes([0x04]) * 12,
            t=3,
            m=32,
            p=4,
            tag_length=32,
        )
        self.assertNotEqual(
            without_secret.hex(),
            "0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659",
        )


class HkdfPublishedVectorTests(SimpleTestCase):
    """RFC 5869 appendix A, test cases 1 and 3."""

    IKM = bytes.fromhex("0b" * 22)

    def test_case_1(self):
        okm = primitives.hkdf_with_salt(
            self.IKM,
            bytes.fromhex("000102030405060708090a0b0c"),
            bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
            42,
        )
        self.assertEqual(
            okm.hex(),
            "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865",
        )

    def test_case_3_zero_length_salt_and_info(self):
        okm = primitives.hkdf_with_salt(self.IKM, b"", b"", 42)
        self.assertEqual(
            okm.hex(),
            "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d"
            "9d201395faa4b61a96c8",
        )

    def test_the_fixed_salt_is_exactly_the_rfc_s_absent_salt(self):
        """RFC 5869 section 2.2: an absent salt is HashLen zero bytes, which for
        SHA-256 is the 32 zero bytes this implementation pins. The fixed salt is
        therefore not a private convention - it is the standard's default,
        written out.
        """
        info = b"v1|unwrap"
        self.assertEqual(
            primitives.hkdf(self.IKM, info),
            primitives.hkdf_with_salt(self.IKM, b"", info, 32),
        )


class Ed25519PublishedVectorTests(SimpleTestCase):
    """RFC 8032 section 7.1, TEST 1 and TEST 2 (pure Ed25519)."""

    VECTORS = [
        (
            "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
            "",
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
        ),
        (
            "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
            "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
            "72",
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
        ),
    ]

    def test_raw_key_encodings_and_signatures_match_the_rfc(self):
        for secret_hex, public_hex, message_hex, signature_hex in self.VECTORS:
            with self.subTest(secret_hex[:16]):
                private = Ed25519PrivateKey.from_private_bytes(
                    bytes.fromhex(secret_hex)
                )
                # public_bytes/private_bytes pick the Raw encoding; the vector
                # is what says Raw is the one the standard means.
                self.assertEqual(
                    primitives.public_bytes(private.public_key()).hex(), public_hex
                )
                self.assertEqual(primitives.private_bytes(private).hex(), secret_hex)
                self.assertEqual(
                    private.sign(bytes.fromhex(message_hex)).hex(), signature_hex
                )

    def test_sign_is_the_anchored_primitive_over_canonical_cbor(self):
        """Ties the wrapper to the anchored signature: one algorithm byte, then
        Ed25519 over the canonical encoding and nothing else.
        """
        private = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(self.VECTORS[0][0])
        )
        payload = {"v": 1, "type": "entry_metadata"}
        signed = primitives.sign(private, payload)
        self.assertEqual(signed[0], primitives.SIG_ALG_ED25519)
        self.assertEqual(signed[1:], private.sign(primitives.canonical_cbor(payload)))


class HpkeSuiteIdentifierTests(SimpleTestCase):
    """The declared suite identifiers against the library's own enum.

    RFC 9180 section 7.3 assigns these numbers, and the design warns explicitly
    that a one-off desynchronizes every wrap. HPKE_SUITE_V1 is documentation
    until something compares it to what the library actually selects, and
    nothing else in the code would notice a mismatch.

    The browser side is covered transitively: its bundle opens what this
    reference sealed, which could not happen under a different suite.
    """

    def test_the_declared_identifiers_match_the_library(self):
        self.assertEqual(
            primitives.HPKE_SUITE_V1["kem_id"], KEMId.DHKEM_X25519_HKDF_SHA256.value
        )
        self.assertEqual(primitives.HPKE_SUITE_V1["kdf_id"], KDFId.HKDF_SHA256.value)
        self.assertEqual(primitives.HPKE_SUITE_V1["aead_id"], AEADId.AES256_GCM.value)
        self.assertEqual(primitives.HPKE_SUITE_V1["mode"], 0x00)

    def test_the_default_suite_is_the_declared_one(self):
        suite = primitives.hpke_suite()
        # .id is the enum member, not an int - these are plain Enums.
        self.assertEqual(suite.kem.id.value, primitives.HPKE_SUITE_V1["kem_id"])
        self.assertEqual(suite.kdf.id.value, primitives.HPKE_SUITE_V1["kdf_id"])
        self.assertEqual(suite.aead.id.value, primitives.HPKE_SUITE_V1["aead_id"])


class HpkePublishedVectorTests(SimpleTestCase):
    """RFC 9180 appendix A.1, base mode.

    The suite is X25519 + HKDF-SHA256 + AES-128-GCM: the RFC publishes no
    X25519 vector for AES-256-GCM, so the AEAD differs from the vault's by one
    identifier. Everything this vector does anchor - the KEM, the key
    serialisation, the info handling and, above all, that a supplied ephemeral
    key pair is the one actually used - is shared with the vault suite.
    """

    INFO = bytes.fromhex("4f6465206f6e2061204772656369616e2055726e")
    SK_E = bytes.fromhex(
        "52c4a758a802cd8b936eceea314432798d5baf2d7e9235dc084ab1b9cfa2f736"
    )
    PK_E = bytes.fromhex(
        "37fda3567bdbd628e88668c3c8d7e97d1d1253b6d4ea6d44c150f741f1bf4431"
    )
    SK_R = bytes.fromhex(
        "4612c550263fc8ad58375df3f557aac531d26850903e55a9f23f21d8534e8ac8"
    )
    PK_R = bytes.fromhex(
        "3948cfe0ad1ddb695d780e59077195da6c56506b027329794ab02bca80815c4d"
    )
    AAD = bytes.fromhex("436f756e742d30")
    PLAINTEXT = bytes.fromhex(
        "4265617574792069732074727574682c20747275746820626561757479"
    )
    CIPHERTEXT = bytes.fromhex(
        "f938558b5d72f1a23810b4be2ab4f84331acc02fc97babc53a52ae8218a355a9"
        "6d8770ac83d07bea87e13c512a"
    )

    def _suite(self):
        return primitives.hpke_suite(
            KEMId.DHKEM_X25519_HKDF_SHA256, KDFId.HKDF_SHA256, AEADId.AES128_GCM
        )

    def test_a_supplied_ephemeral_key_is_the_one_encapsulated(self):
        """The whole reproducibility of the vault's HPKE vector rests on `eks`
        controlling the ephemeral key. If it did not - if it were ignored, or
        if it silently selected authenticated mode - `enc` would be a fresh
        public key instead of the vector's, and this fails.
        """
        suite = self._suite()
        ephemeral = KEMKeyPair(
            suite.kem.deserialize_private_key(self.SK_E),
            suite.kem.deserialize_public_key(self.PK_E),
        )
        enc, sender = suite.create_sender_context(
            pkr=suite.kem.deserialize_public_key(self.PK_R),
            info=self.INFO,
            eks=ephemeral,
        )
        self.assertEqual(enc.hex(), self.PK_E.hex())
        self.assertEqual(sender.seal(self.PLAINTEXT, aad=self.AAD), self.CIPHERTEXT)

    def test_the_recipient_side_opens_the_rfc_ciphertext(self):
        suite = self._suite()
        recipient = suite.create_recipient_context(
            enc=self.PK_E,
            skr=suite.kem.deserialize_private_key(self.SK_R),
            info=self.INFO,
        )
        self.assertEqual(recipient.open(self.CIPHERTEXT, aad=self.AAD), self.PLAINTEXT)


class TotpPublishedVectorTests(SimpleTestCase):
    """RFC 6238 appendix B.

    The three modes use three different seeds: the ASCII string
    "12345678901234567890" repeated up to the digest length. Feeding one seed
    to all three reproduces the SHA-1 column and nothing else, which is the
    usual way this table appears to be wrong.
    """

    SEED = "12345678901234567890"
    # T0 = 0, period 30, 8 digits.
    VECTORS = [
        (59, "94287082", "46119246", "90693936"),
        (1111111109, "07081804", "68084774", "25091201"),
        (1111111111, "14050471", "67062674", "99943326"),
        (1234567890, "89005924", "91819424", "93441116"),
        (2000000000, "69279037", "90698825", "38618901"),
        (20000000000, "65353130", "77737706", "47863826"),
    ]
    LENGTHS = {"SHA1": 20, "SHA256": 32, "SHA512": 64}

    def _secret(self, algorithm: str) -> bytes:
        length = self.LENGTHS[algorithm]
        repeated = self.SEED * (length // len(self.SEED) + 1)
        return repeated[:length].encode()

    def test_the_rfc_6238_table_is_reproduced(self):
        for at, sha1, sha256, sha512 in self.VECTORS:
            for algorithm, expected in (
                ("SHA1", sha1),
                ("SHA256", sha256),
                ("SHA512", sha512),
            ):
                with self.subTest(at=at, algorithm=algorithm):
                    self.assertEqual(
                        totp.totp_code(
                            self._secret(algorithm),
                            algorithm=algorithm,
                            digits=8,
                            period=30,
                            at=at,
                        ),
                        expected,
                    )

    def test_the_seed_length_is_what_separates_the_three_columns(self):
        """Guards the guard: with one seed for all three, the SHA-256
        column could only pass by coincidence."""
        at, _, sha256, _ = self.VECTORS[0]
        self.assertNotEqual(
            totp.totp_code(
                self._secret("SHA1"), algorithm="SHA256", digits=8, period=30, at=at
            ),
            sha256,
        )

    def test_base32_round_trips_the_rfc_seed(self):
        secret = self._secret("SHA1")
        self.assertEqual(
            totp.base32_decode("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"), secret
        )

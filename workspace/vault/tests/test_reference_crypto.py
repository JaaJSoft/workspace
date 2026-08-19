from cryptography.exceptions import InvalidSignature, InvalidTag
from django.test import SimpleTestCase
from pyhpke.exceptions import OpenError

from workspace.vault.tests.reference import ad, encoding, primitives, wire


class Base64UrlTests(SimpleTestCase):
    def test_round_trips_without_padding(self):
        for length in range(0, 8):
            data = bytes(range(length))
            text = encoding.to_base64url(data)
            self.assertNotIn("=", text)
            self.assertEqual(encoding.from_base64url(text), data)

    def test_uses_the_url_safe_alphabet(self):
        # 0xFB 0xFF encodes to "+/8" in the standard alphabet; the URL-safe one
        # is what the wire format mandates because these strings travel in JSON
        # and in query parameters.
        self.assertEqual(encoding.to_base64url(b"\xfb\xff"), "-_8")


class WireFormatTests(SimpleTestCase):
    def _sample(self, **overrides):
        fields = {
            "aead_id": wire.AEAD_AES_256_GCM,
            "kdf_id": wire.KDF_HKDF_SHA256,
            "key_version": 1,
            "iv": bytes(range(12)),
            "ciphertext": b"ciphertext-and-tag",
        }
        fields.update(overrides)
        return wire.encode_ciphertext(**fields)

    def test_header_is_six_bytes_then_iv_then_ciphertext(self):
        raw = self._sample()
        self.assertEqual(raw[0], 0x01)  # format_version
        self.assertEqual(raw[1], wire.AEAD_AES_256_GCM)
        self.assertEqual(raw[2], wire.KDF_HKDF_SHA256)
        self.assertEqual(raw[3:5], b"\x00\x01")  # key_version, big endian
        self.assertEqual(raw[5], 12)  # iv_len
        self.assertEqual(raw[6:18], bytes(range(12)))
        self.assertEqual(raw[18:], b"ciphertext-and-tag")

    def test_round_trips(self):
        decoded = wire.decode_ciphertext(self._sample(key_version=513))
        self.assertEqual(decoded.key_version, 513)
        self.assertEqual(decoded.iv, bytes(range(12)))
        self.assertEqual(decoded.ciphertext, b"ciphertext-and-tag")

    def test_rejects_an_unknown_format_version(self):
        """A foreign format_version is rejected before any parsing at all, so a
        future layout can never be half-read by an old client.
        """
        raw = bytearray(self._sample())
        raw[0] = 0x02
        with self.assertRaises(wire.UnsupportedVersion):
            wire.decode_ciphertext(bytes(raw))

    def test_rejects_an_iv_length_inconsistent_with_the_aead(self):
        raw = bytearray(self._sample())
        raw[5] = 24  # XChaCha length declared on an AES-GCM ciphertext
        with self.assertRaises(ValueError):
            wire.decode_ciphertext(bytes(raw))

    def test_rejects_a_key_version_that_does_not_fit_two_bytes(self):
        with self.assertRaises(ValueError):
            self._sample(key_version=65536)


class AssociatedDataTests(SimpleTestCase):
    ENTRY = "0192f3a4-5b6c-7d8e-9f01-23456789abcd"
    USER = "0192f3a4-1111-7d8e-9f01-23456789abcd"
    VAULT = "0192f3a4-2222-7d8e-9f01-23456789abcd"

    def test_the_catalogue_matches_the_norm_byte_for_byte(self):
        """These strings are the contract: changing one silently breaks the
        decryption of everything already written with it.
        """
        self.assertEqual(ad.unwrap_info(), b"v1|unwrap")
        self.assertEqual(
            ad.entry_key_info(self.ENTRY), f"v1|entry-key|{self.ENTRY}".encode()
        )
        self.assertEqual(
            ad.kex_priv_ad(self.USER), f"v1|account-kex-priv|{self.USER}".encode()
        )
        self.assertEqual(
            ad.sig_priv_ad(self.USER), f"v1|account-sig-priv|{self.USER}".encode()
        )
        self.assertEqual(
            ad.entry_field_ad(self.ENTRY, "password"),
            f"v1|entry-field|{self.ENTRY}|password".encode(),
        )
        self.assertEqual(
            ad.kex_pub_payload(self.USER, "AWtleA"),
            f"v1|account-kex-pub|{self.USER}|AWtleA".encode(),
        )
        self.assertEqual(
            ad.vault_key_info(self.VAULT, self.USER),
            f"v1|vault-key|{self.VAULT}|{self.USER}".encode(),
        )

    def test_uuids_are_lowercased(self):
        # RFC 4122 with dashes, lowercase - a caller passing an uppercase UUID
        # would otherwise derive a different key for the same entry.
        self.assertEqual(
            ad.entry_key_info(self.ENTRY.upper()),
            f"v1|entry-key|{self.ENTRY}".encode(),
        )

    def test_every_string_is_ascii_without_a_trailing_newline(self):
        for value in (
            ad.unwrap_info(),
            ad.entry_key_info(self.ENTRY),
            ad.entry_field_ad(self.ENTRY, "password"),
            ad.vault_key_info(self.VAULT, self.USER),
        ):
            value.decode("ascii")
            self.assertFalse(value.endswith(b"\n"))


class FieldIdTests(SimpleTestCase):
    def test_reserved_identifiers_pass_through(self):
        for field_id in ("username", "password", "totp", "uri"):
            self.assertEqual(ad.qualify_field_id(field_id), field_id)

    def test_user_defined_identifiers_are_prefixed(self):
        self.assertEqual(ad.qualify_field_id("recovery-code"), "custom:recovery-code")

    def test_name_and_notes_can_never_be_produced_for_an_entry_field(self):
        """`name` and `notes` are the associated data of VaultEntry columns
        living in another table. An EntryField deriving the same AD would let a
        ciphertext be swapped between the two and still verify - the exact
        attack the AD exists to close.
        """
        for reserved in ("name", "notes"):
            self.assertEqual(ad.qualify_field_id(reserved), f"custom:{reserved}")

    def test_an_already_prefixed_identifier_is_not_prefixed_twice(self):
        self.assertEqual(
            ad.qualify_field_id("custom:recovery-code"), "custom:recovery-code"
        )


class Argon2Tests(SimpleTestCase):
    def test_the_secret_key_changes_the_result(self):
        """The secret_key is passed as Argon2's K parameter (RFC 9106 §3.1),
        not concatenated with the password. If it were being ignored, these two
        derivations would collide - which is the whole failure this test exists
        to catch, because argon2-cffi's hash_secret_raw() silently drops it.
        """
        salt = bytes(range(32))
        first = primitives.derive_amk("Tr0ub4dor&3", bytes(32), salt)
        second = primitives.derive_amk("Tr0ub4dor&3", bytes([1]) + bytes(31), salt)
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_the_password_is_nfc_normalised(self):
        """ "café" precomposed and "café" decomposed must derive the same AMK,
        or the same password typed on two keyboards opens two different vaults.
        """
        # The two strings below are indistinguishable in an editor: the
        # first is U+00E9, the second is e + U+0301. A well-meaning cleanup
        # that removes the apparent duplicate deletes the point of the
        # test, which is what the assertNotEqual guards against.
        precomposed = "café"
        decomposed = "café"
        self.assertNotEqual(precomposed, decomposed)
        salt = bytes(range(32))
        self.assertEqual(
            primitives.derive_amk(precomposed, bytes(32), salt),
            primitives.derive_amk(decomposed, bytes(32), salt),
        )


class AeadTests(SimpleTestCase):
    KEY = bytes(range(32))
    IV = bytes(range(12))

    def _seal(self, associated_data=b"v1|entry-field|x|password"):
        return primitives.aead_seal(
            self.KEY,
            b"hunter2",
            associated_data,
            iv=self.IV,
            key_version=1,
            kdf_id=0x01,
        )

    def test_round_trips(self):
        raw = self._seal()
        self.assertEqual(
            primitives.aead_open(self.KEY, raw, b"v1|entry-field|x|password"),
            b"hunter2",
        )

    def test_rejects_a_substituted_associated_data(self):
        """Moving a ciphertext from one field to another must fail to open -
        this is what the AD is for.
        """
        raw = self._seal()
        # InvalidTag, not Exception: a test that accepts any failure would also
        # pass if aead_open started raising on its own arguments.
        with self.assertRaises(InvalidTag):
            primitives.aead_open(self.KEY, raw, b"v1|entry-field|x|username")


class CborTests(SimpleTestCase):
    def test_map_keys_are_sorted_by_their_cbor_encoding(self):
        """Not by string comparison: CBOR sorts by encoded bytes, so a shorter
        key sorts before a longer one whatever the letters (RFC 8949 §4.2.1).
        """
        encoded = primitives.canonical_cbor({"bb": 1, "a": 2})
        self.assertLess(encoded.index(b"a"), encoded.index(b"bb"))

    def test_rejects_a_float(self):
        """Floats are forbidden: two implementations round differently and the
        signature stops matching. Every quantity is an integer, timestamps in
        milliseconds.
        """
        with self.assertRaises(ValueError):
            primitives.canonical_cbor({"v": 1, "t": 1.5})

    def test_is_stable_across_key_insertion_order(self):
        self.assertEqual(
            primitives.canonical_cbor({"v": 1, "type": "test"}),
            primitives.canonical_cbor({"type": "test", "v": 1}),
        )


class SignatureTests(SimpleTestCase):
    def test_signature_carries_its_algorithm_prefix(self):
        private = primitives.generate_sig_keypair()
        signature = primitives.sign(private, {"v": 1, "type": "entry_metadata"})
        self.assertEqual(signature[0], primitives.SIG_ALG_ED25519)
        self.assertEqual(len(signature), 1 + 64)

    def test_verification_rejects_a_replayed_type(self):
        """Step 3 of the five-step protocol: an entry_metadata signature
        replayed as a share_record must fail before Ed25519 is even consulted.
        """
        private = primitives.generate_sig_keypair()
        payload = {"v": 1, "type": "entry_metadata"}
        signature = primitives.sign(private, payload)
        with self.assertRaises(ValueError):
            primitives.verify(
                private.public_key(),
                primitives.canonical_cbor(payload),
                signature,
                expected_type="share_record",
            )

    def test_verification_rejects_a_non_canonical_encoding(self):
        """Step 4: re-canonicalise and compare byte for byte, so a payload that
        parses but was not canonically encoded is refused.
        """
        private = primitives.generate_sig_keypair()
        payload = {"v": 1, "type": "entry_metadata"}
        canonical = primitives.canonical_cbor(payload)
        signature = primitives.sign(private, payload)
        # An indefinite-length map: decodable, but not the canonical form.
        non_canonical = b"\xbf" + canonical[1:] + b"\xff"
        with self.assertRaises(ValueError):
            primitives.verify(
                private.public_key(),
                non_canonical,
                signature,
                expected_type="entry_metadata",
            )

    def test_verification_rejects_a_tampered_signature(self):
        private = primitives.generate_sig_keypair()
        payload = {"v": 1, "type": "entry_metadata"}
        signature = bytearray(primitives.sign(private, payload))
        signature[-1] ^= 0x01
        with self.assertRaises(InvalidSignature):
            primitives.verify(
                private.public_key(),
                primitives.canonical_cbor(payload),
                bytes(signature),
                expected_type="entry_metadata",
            )


class HpkeTests(SimpleTestCase):
    def test_round_trips_with_a_pinned_sender_key(self):
        recipient_private = primitives.generate_kex_keypair()
        sender_private = primitives.generate_kex_keypair()
        info = b"v1|vault-key|vault|recipient"
        vault_key = bytes(range(32))
        sealed = primitives.hpke_seal(
            recipient_private.public_key(),
            info,
            vault_key,
            sender_private=sender_private,
        )
        self.assertEqual(
            primitives.hpke_open(recipient_private, info, sealed), vault_key
        )

    def test_a_different_info_fails_to_open(self):
        """All context binding goes through info, aad stays empty - so info is
        the only thing standing between a wrap for vault A and vault B.
        """
        recipient_private = primitives.generate_kex_keypair()
        sender_private = primitives.generate_kex_keypair()
        sealed = primitives.hpke_seal(
            recipient_private.public_key(),
            b"v1|vault-key|a|r",
            bytes(range(32)),
            sender_private=sender_private,
        )
        with self.assertRaises(OpenError):
            primitives.hpke_open(recipient_private, b"v1|vault-key|b|r", sealed)


class CanonicalTypeTests(SimpleTestCase):
    """The accepted types are closed, and match the browser's exactly."""

    def test_a_byte_string_encodes_untagged(self):
        self.assertEqual(
            primitives.canonical_cbor({"v": 1, "k": b"\x01\x02\x03"}).hex(),
            "a2616b43010203617601",
        )

    def test_a_type_with_no_agreed_encoding_is_refused(self):
        for value in ({1, 2}, object(), 1.5):
            with self.subTest(type(value).__name__):
                with self.assertRaises(ValueError):
                    primitives.canonical_cbor({"v": 1, "x": value})

    def test_a_non_string_map_key_is_refused(self):
        with self.assertRaises(ValueError):
            primitives.canonical_cbor({1: "one"})

    def test_associated_data_outside_ascii_is_refused(self):
        with self.assertRaises(UnicodeEncodeError):
            ad.entry_field_ad("0192f3a4-5b6c-7d8e-9f01-23456789abcd", "caf\u00e9")


class AeadKeyLengthTests(SimpleTestCase):
    def test_a_key_of_the_wrong_length_is_refused(self):
        """AESGCM infers the variant from the key length, so a short key would
        produce AES-128-GCM under a header still declaring AES-256-GCM.
        """
        for length in (16, 24, 31):
            with self.subTest(length):
                with self.assertRaises(ValueError):
                    primitives.aead_seal(
                        bytes(length),
                        b"x",
                        b"ad",
                        iv=bytes(12),
                        key_version=1,
                        kdf_id=0x01,
                    )


class TruncatedCiphertextTests(SimpleTestCase):
    def test_a_ciphertext_truncated_inside_its_iv_is_refused(self):
        raw = wire.encode_ciphertext(
            aead_id=wire.AEAD_AES_256_GCM,
            kdf_id=wire.KDF_HKDF_SHA256,
            key_version=1,
            iv=bytes(12),
            ciphertext=b"tag",
        )
        with self.assertRaises(ValueError):
            wire.decode_ciphertext(raw[:8])

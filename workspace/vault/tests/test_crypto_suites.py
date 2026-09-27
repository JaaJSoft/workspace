from cryptography.exceptions import InvalidTag
from django.test import SimpleTestCase

from .reference import ad, primitives, suites, wire

KEY = bytes(range(32))
ENTRY = "01890a5d-ac96-774b-bcce-b302099a8057"


class ManifestShapeTests(SimpleTestCase):
    def test_every_axis_has_exactly_one_current_entry(self):
        for axis, entries in suites.manifest().items():
            if axis == "pubkey":
                continue
            with self.subTest(axis=axis):
                current = [k for k, v in entries.items() if v["state"] == "current"]
                self.assertEqual(len(current), 1, current)

    def test_public_keys_have_one_current_entry_per_usage(self):
        entries = suites.manifest()["pubkey"].values()
        for usage in ("kex", "sig"):
            with self.subTest(usage=usage):
                current = [e for e in entries if e["state"] == "current" and e["usage"] == usage]
                self.assertEqual(len(current), 1)

    def test_states_come_from_the_closed_list(self):
        for axis, entries in suites.manifest().items():
            for key, value in entries.items():
                with self.subTest(axis=axis, key=key):
                    self.assertIn(value["state"], {"current", "superseded", "test"})

    def test_the_current_suite_is_format_2_aes_gcm(self):
        self.assertEqual(suites.CURRENT_SUITE["format_version"], 2)
        self.assertEqual(suites.CURRENT_SUITE["aead_id"], 0x01)
        self.assertEqual(suites.CURRENT_SUITE["hpke"]["format"], 2)
        self.assertEqual(suites.CURRENT_SUITE["kdf"]["algo"], "argon2id")
        self.assertNotIn("algo", suites.CURRENT_SUITE["kdf"]["params"])


class WireHeaderDispatchTests(SimpleTestCase):
    def _raw(self, format_version=2, aead_id=0x01, iv_len=12):
        return bytes([format_version, aead_id, 0x01, 0x00, 0x01, iv_len]) + bytes(iv_len) + b"ct"

    def test_both_formats_decode(self):
        for format_version in (1, 2):
            with self.subTest(format_version=format_version):
                decoded = wire.decode_ciphertext(self._raw(format_version=format_version))
                self.assertEqual(decoded.format_version, format_version)
                self.assertEqual(decoded.header, self._raw(format_version=format_version)[:6])

    def test_an_unknown_format_is_unsupported(self):
        with self.assertRaises(suites.UnsupportedAlgorithm) as caught:
            wire.decode_ciphertext(self._raw(format_version=3))
        self.assertEqual((caught.exception.axis, caught.exception.identifier), ("format", 3))

    def test_an_unknown_aead_is_unsupported(self):
        with self.assertRaises(suites.UnsupportedAlgorithm) as caught:
            wire.decode_ciphertext(self._raw(aead_id=0x07))
        self.assertEqual((caught.exception.axis, caught.exception.identifier), ("aead", 7))

    def test_the_test_aead_declares_a_sixteen_byte_nonce(self):
        decoded = wire.decode_ciphertext(self._raw(aead_id=0xF0, iv_len=16))
        self.assertEqual(len(decoded.iv), 16)

    def test_a_nonce_length_the_aead_does_not_declare_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            wire.decode_ciphertext(self._raw(aead_id=0x01, iv_len=16))
        self.assertNotIsInstance(caught.exception, suites.UnsupportedAlgorithm)

    def test_encoding_defaults_to_the_current_format(self):
        raw = wire.encode_ciphertext(
            aead_id=0x01, kdf_id=0x01, key_version=1, iv=bytes(12), ciphertext=b"x"
        )
        self.assertEqual(raw[0], 2)


class AssociatedDataTests(SimpleTestCase):
    def test_format_1_is_the_v1_string_alone(self):
        header = bytes([1, 1, 1, 0, 1, 12])
        self.assertEqual(
            ad.associated_data(ad.entry_field_ad(ENTRY, "password"), header),
            f"v1|entry-field|{ENTRY}|password".encode(),
        )

    def test_format_2_prepends_the_header_to_the_v2_string(self):
        header = bytes([2, 1, 1, 0, 1, 12])
        self.assertEqual(
            ad.associated_data(ad.entry_field_ad(ENTRY, "password"), header),
            header + f"v2|entry-field|{ENTRY}|password".encode(),
        )

    def test_caller_owned_bytes_get_the_header_in_format_2_only(self):
        self.assertEqual(ad.associated_data(b"raw", bytes([1, 1, 1, 0, 0, 12])), b"raw")
        header = bytes([2, 1, 1, 0, 0, 12])
        self.assertEqual(ad.associated_data(b"raw", header), header + b"raw")


class AeadDispatchTests(SimpleTestCase):
    def _seal(self, **kwargs):
        iv = bytes(primitives.AEADS[kwargs.get("aead_id", 1)].iv_length)
        return primitives.aead_seal(
            KEY, b"secret", ad.entry_field_ad(ENTRY, "password"),
            iv=iv, key_version=1, kdf_id=1, **kwargs,
        )

    def test_three_kinds_of_ciphertext_open_side_by_side(self):
        for kwargs in ({"format_version": 1}, {"format_version": 2}, {"aead_id": 0xF0}):
            with self.subTest(**kwargs):
                raw = self._seal(**kwargs)
                self.assertEqual(
                    primitives.aead_open(KEY, raw, ad.entry_field_ad(ENTRY, "password")),
                    b"secret",
                )

    def test_the_test_aead_is_not_aes_gcm_under_another_number(self):
        raw = bytearray(self._seal(aead_id=0xF0))
        raw[1] = 0x01  # claim AES-GCM
        raw[5] = 12
        with self.assertRaises((InvalidTag, ValueError)):
            primitives.aead_open(KEY, bytes(raw), ad.entry_field_ad(ENTRY, "password"))

    def test_a_format_2_header_byte_is_authenticated(self):
        for index in (2, 3, 4):  # kdf_id, key_version high, key_version low
            with self.subTest(index=index):
                raw = bytearray(self._seal(format_version=2))
                raw[index] ^= 0x01
                with self.assertRaises(InvalidTag):
                    primitives.aead_open(KEY, bytes(raw), ad.entry_field_ad(ENTRY, "password"))

    def test_a_format_1_header_byte_is_not(self):
        # The limit format 2 exists to close: kdf_id and key_version are
        # outside format 1's associated data, so flipping one goes unnoticed.
        raw = bytearray(self._seal(format_version=1))
        raw[2] ^= 0x01
        self.assertEqual(
            primitives.aead_open(KEY, bytes(raw), ad.entry_field_ad(ENTRY, "password")),
            b"secret",
        )

    def test_the_test_aead_never_uses_the_key_as_it_is(self):
        # Same 32 bytes, two algorithms: its working keys are HKDF children.
        aead = primitives.AEADS[0xF0]
        self.assertNotIn(KEY, aead.working_keys(KEY))

    def test_the_reference_implements_every_aead_the_manifest_declares(self):
        # The reference reads every state, "test" included.
        self.assertEqual(
            sorted(primitives.AEADS), sorted(int(k) for k in suites.manifest()["aead"])
        )

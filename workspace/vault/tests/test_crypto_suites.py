from django.test import SimpleTestCase

from .reference import suites, wire


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

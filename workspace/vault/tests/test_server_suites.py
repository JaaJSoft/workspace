from django.test import SimpleTestCase, override_settings

from ..services import suites


def _ciphertext(format_version=2, aead_id=1, iv_len=12):
    return bytes([format_version, aead_id, 1, 0, 1, iv_len]) + bytes(iv_len) + bytes(17)


class CiphertextCheckTests(SimpleTestCase):
    def test_both_formats_are_accepted(self):
        # A tab opened before the deploy still writes format 1.
        for format_version in (1, 2):
            with self.subTest(format_version=format_version):
                suites.check_ciphertext(_ciphertext(format_version=format_version))

    def test_unknown_format_aead_and_nonce_length_are_refused(self):
        for raw in (
            _ciphertext(format_version=3),
            _ciphertext(aead_id=7),
            _ciphertext(iv_len=16),
            b"\x02\x01",
        ):
            with (
                self.subTest(raw=raw[:6]),
                self.assertRaises(suites.UnsupportedCiphertext),
            ):
                suites.check_ciphertext(raw)

    def test_a_ciphertext_shorter_than_its_nonce_is_refused(self):
        with self.assertRaises(suites.UnsupportedCiphertext):
            suites.check_ciphertext(_ciphertext()[:10])

    @override_settings(VAULT_TEST_SUITES=False)
    def test_the_test_aead_is_refused_outside_the_test_switch(self):
        with self.assertRaises(suites.UnsupportedCiphertext):
            suites.check_ciphertext(_ciphertext(aead_id=0xF0, iv_len=16))

    @override_settings(VAULT_TEST_SUITES=True)
    def test_the_test_aead_is_accepted_under_it(self):
        suites.check_ciphertext(_ciphertext(aead_id=0xF0, iv_len=16))


class SuiteCheckTests(SimpleTestCase):
    FORMAT_1 = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0}

    def test_hpke_suites_of_both_formats_pass_and_others_do_not(self):
        suites.check_hpke_suite(self.FORMAT_1)
        suites.check_hpke_suite({**self.FORMAT_1, "format": 2})
        for bad in (
            {**self.FORMAT_1, "format": 9},
            {**self.FORMAT_1, "aead_id": 3},
            {**self.FORMAT_1, "x": 1},
            {"kem_id": 32, "kdf_id": 1, "aead_id": 2},
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                suites.check_hpke_suite(bad)

    def test_format_1_is_spelled_by_the_keys_absence(self):
        """An explicit 1 pairs a format-1 suite with an info naming its suite,
        which no writer ever sealed under."""
        with self.assertRaises(ValueError):
            suites.check_hpke_suite({**self.FORMAT_1, "format": 1})

    def test_the_format_and_the_ids_must_be_genuine_integers(self):
        """True == 1 and 2.0 == 2 in Python, so equality alone would let either
        stand in for the declared id."""
        for bad in (
            {**self.FORMAT_1, "format": "2"},
            {**self.FORMAT_1, "format": 2.0},
            {**self.FORMAT_1, "format": True},
            {**self.FORMAT_1, "kdf_id": True},
            {**self.FORMAT_1, "kem_id": 32.0},
            {**self.FORMAT_1, "kem_id": "32"},
            {**self.FORMAT_1, "format": 2, "kdf_id": True},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                suites.check_hpke_suite(bad)

    def test_account_kdf_bounds(self):
        params = {"v": "1.3", "m": 65536, "t": 3, "p": 2}
        suites.check_account_kdf("argon2id", params)
        # Seeded development accounts carry an extra "algo" key.
        suites.check_account_kdf("argon2id", {**params, "algo": "argon2id"})
        for algo, bad in (
            ("scrypt", params),
            ("argon2id", {**params, "m": 4 * 1024 * 1024}),
            ("argon2id", {**params, "t": True}),
            ("argon2id", {**params, "v": "1.0"}),
            ("argon2id", {"m": 65536, "t": 3, "p": 2}),
            ("argon2id", None),
        ):
            with self.subTest(algo=algo, bad=bad), self.assertRaises(ValueError):
                suites.check_account_kdf(algo, bad)


class LengthLookupTests(SimpleTestCase):
    def test_declared_lengths_come_from_the_manifest(self):
        self.assertEqual(suites.pubkey_length(1), 32)
        self.assertEqual(suites.pubkey_length(2), 32)
        self.assertEqual(suites.signature_length(1), 64)

    def test_an_undeclared_id_has_no_length(self):
        self.assertIsNone(suites.pubkey_length(9))
        self.assertIsNone(suites.signature_length(9))


class ServerRegistryParityTests(SimpleTestCase):
    def test_every_readable_signature_id_has_a_verifier_and_no_other(self):
        from ..services import attestation

        declared = {
            int(key)
            for key, value in suites._manifest()["signature"].items()
            if value["state"] != "test"
        }
        self.assertEqual(set(attestation._SIGNATURE_VERIFIERS), declared)

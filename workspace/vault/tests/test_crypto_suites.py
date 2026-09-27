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
                current = [
                    e
                    for e in entries
                    if e["state"] == "current" and e["usage"] == usage
                ]
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
        return (
            bytes([format_version, aead_id, 0x01, 0x00, 0x01, iv_len])
            + bytes(iv_len)
            + b"ct"
        )

    def test_both_formats_decode(self):
        for format_version in (1, 2):
            with self.subTest(format_version=format_version):
                decoded = wire.decode_ciphertext(
                    self._raw(format_version=format_version)
                )
                self.assertEqual(decoded.format_version, format_version)
                self.assertEqual(
                    decoded.header, self._raw(format_version=format_version)[:6]
                )

    def test_an_unknown_format_is_unsupported(self):
        with self.assertRaises(suites.UnsupportedAlgorithm) as caught:
            wire.decode_ciphertext(self._raw(format_version=3))
        self.assertEqual(
            (caught.exception.axis, caught.exception.identifier), ("format", 3)
        )

    def test_an_unknown_aead_is_unsupported(self):
        with self.assertRaises(suites.UnsupportedAlgorithm) as caught:
            wire.decode_ciphertext(self._raw(aead_id=0x07))
        self.assertEqual(
            (caught.exception.axis, caught.exception.identifier), ("aead", 7)
        )

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
            KEY,
            b"secret",
            ad.entry_field_ad(ENTRY, "password"),
            iv=iv,
            key_version=1,
            kdf_id=1,
            **kwargs,
        )

    def test_three_kinds_of_ciphertext_open_side_by_side(self):
        for kwargs in ({"format_version": 1}, {"format_version": 2}, {"aead_id": 0xF0}):
            with self.subTest(**kwargs):
                raw = self._seal(**kwargs)
                self.assertEqual(
                    primitives.aead_open(
                        KEY, raw, ad.entry_field_ad(ENTRY, "password")
                    ),
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
                    primitives.aead_open(
                        KEY, bytes(raw), ad.entry_field_ad(ENTRY, "password")
                    )

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


ACCOUNT = "01890a5d-ac96-774b-bcce-b302099a8058"
VAULT = "01890a5d-ac96-774b-bcce-b302099a8059"
FORMAT_1_HPKE = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0}
FORMAT_2_HPKE = {**FORMAT_1_HPKE, "format": 2}


class HpkeDispatchTests(SimpleTestCase):
    def setUp(self):
        self.recipient = primitives.generate_kex_keypair()
        self.sender = primitives.generate_kex_keypair()

    def _wrap(self, suite):
        info = ad.vault_key_info(VAULT, ACCOUNT, suite)
        return info, primitives.hpke_seal(
            self.recipient.public_key(),
            info,
            KEY,
            sender_private=self.sender,
            hpke_suite=suite,
        )

    def test_the_format_2_info_names_the_suite(self):
        self.assertEqual(
            ad.vault_key_info(VAULT, ACCOUNT, FORMAT_2_HPKE),
            f"v2|vault-key|{VAULT}|{ACCOUNT}|0020-0001-0002".encode(),
        )
        self.assertEqual(
            ad.vault_key_info(VAULT, ACCOUNT, FORMAT_1_HPKE),
            f"v1|vault-key|{VAULT}|{ACCOUNT}".encode(),
        )

    def test_both_formats_open(self):
        for suite in (FORMAT_1_HPKE, FORMAT_2_HPKE):
            with self.subTest(suite=suite):
                info, sealed = self._wrap(suite)
                self.assertEqual(
                    primitives.hpke_open(
                        self.recipient, info, sealed, hpke_suite=suite
                    ),
                    KEY,
                )

    def test_dropping_the_format_to_downgrade_a_wrap_fails(self):
        _, sealed = self._wrap(FORMAT_2_HPKE)
        downgraded_info = ad.vault_key_info(VAULT, ACCOUNT, FORMAT_1_HPKE)
        with self.assertRaises(Exception):
            primitives.hpke_open(
                self.recipient, downgraded_info, sealed, hpke_suite=FORMAT_1_HPKE
            )

    def test_an_unknown_suite_is_unsupported(self):
        for stored in ({**FORMAT_2_HPKE, "format": 9}, {**FORMAT_2_HPKE, "aead_id": 3}):
            with (
                self.subTest(stored=stored),
                self.assertRaises(suites.UnsupportedAlgorithm),
            ):
                primitives.hpke_suite_for(stored)

    def test_an_explicit_format_1_or_a_string_format_is_unsupported(self):
        # Format 1 is the missing key: vault_key_info would give an explicit 1
        # an info naming the suite, which no format-1 wrap was sealed under.
        for stored in (
            {**FORMAT_1_HPKE, "format": 1},
            {**FORMAT_1_HPKE, "format": "2"},
        ):
            with (
                self.subTest(stored=stored),
                self.assertRaises(suites.UnsupportedAlgorithm),
            ):
                primitives.hpke_suite_for(stored)

    def test_a_suite_id_spelled_as_a_bool_or_a_string_is_unsupported(self):
        # True == 1 and 2.0 == 2 in Python: a dict comparison alone lets them pass.
        for stored in (
            {**FORMAT_2_HPKE, "kdf_id": True},
            {**FORMAT_1_HPKE, "kdf_id": True},
            {**FORMAT_2_HPKE, "kem_id": "32"},
            {**FORMAT_2_HPKE, "aead_id": 2.0},
            {**FORMAT_2_HPKE, "format": True},
        ):
            with (
                self.subTest(stored=stored),
                self.assertRaises(suites.UnsupportedAlgorithm),
            ):
                primitives.hpke_suite_for(stored)

    def test_the_info_applies_the_same_rule_as_the_opener(self):
        for stored in (
            {**FORMAT_1_HPKE, "format": 1},
            {**FORMAT_1_HPKE, "format": "2"},
            {**FORMAT_2_HPKE, "kdf_id": True},
            {**FORMAT_2_HPKE, "aead_id": 3},
        ):
            with (
                self.subTest(stored=stored),
                self.assertRaises(suites.UnsupportedAlgorithm),
            ):
                ad.vault_key_info(VAULT, ACCOUNT, stored)


class SignatureAndKeyDispatchTests(SimpleTestCase):
    def test_an_unknown_signature_prefix_is_unsupported(self):
        key = primitives.generate_sig_keypair()
        signature = bytearray(primitives.sign_bytes(key, b"message"))
        signature[0] = 0x09
        with self.assertRaises(suites.UnsupportedAlgorithm):
            primitives.verify_bytes(key.public_key(), b"message", bytes(signature))

    def test_an_unknown_payload_version_is_unsupported(self):
        key = primitives.generate_sig_keypair()
        payload = {"v": 7, "type": "tag-metadata"}
        data = primitives.canonical_cbor(payload)
        with self.assertRaises(suites.UnsupportedAlgorithm):
            primitives.verify(
                key.public_key(), data, primitives.sign_bytes(key, data), "tag-metadata"
            )

    def test_a_payload_version_spelled_as_a_string_is_unsupported(self):
        # The manifest is keyed by strings: "1" must not find the entry 1 names.
        key = primitives.generate_sig_keypair()
        data = primitives.canonical_cbor({"v": "1", "type": "tag-metadata"})
        with self.assertRaises(suites.UnsupportedAlgorithm):
            primitives.verify(
                key.public_key(), data, primitives.sign_bytes(key, data), "tag-metadata"
            )

    def test_a_signature_of_the_wrong_length_is_refused_before_ed25519(self):
        key = primitives.generate_sig_keypair()
        signature = primitives.sign_bytes(key, b"message")
        with self.assertRaises(ValueError):
            primitives.verify_bytes(key.public_key(), b"message", signature + b"\x00")

    def test_a_public_key_is_checked_against_its_usage(self):
        sig_key = primitives.generate_sig_keypair()
        stored = primitives.encode_public_key(
            sig_key.public_key(), suites.CURRENT_SUITE["sig_public_key_alg"]
        )
        self.assertEqual(len(primitives.decode_public_key(stored, "sig")), 32)
        with self.assertRaises(ValueError) as caught:
            primitives.decode_public_key(stored, "kex")
        self.assertNotIsInstance(caught.exception, suites.UnsupportedAlgorithm)
        with self.assertRaises(suites.UnsupportedAlgorithm):
            primitives.decode_public_key(bytes([0x07]) + bytes(32), "kex")


class AccountKdfTests(SimpleTestCase):
    PARAMS = {"v": "1.3", "m": 65536, "t": 3, "p": 2}

    def test_the_current_parameters_pass(self):
        primitives.assert_account_kdf("argon2id", self.PARAMS)

    def test_a_seeded_account_with_an_algo_key_still_passes(self):
        primitives.assert_account_kdf("argon2id", {**self.PARAMS, "algo": "argon2id"})

    def test_unknown_or_out_of_bounds_is_unsupported(self):
        for algo, params in (
            ("scrypt", self.PARAMS),
            ("argon2id", {**self.PARAMS, "v": "1.0"}),
            ("argon2id", {**self.PARAMS, "m": 4 * 1024 * 1024}),
            ("argon2id", {**self.PARAMS, "t": 0}),
            ("argon2id", {**self.PARAMS, "p": 5}),
        ):
            with self.subTest(algo=algo, params=params):
                with self.assertRaises(suites.UnsupportedAlgorithm):
                    primitives.assert_account_kdf(algo, params)

    def test_a_bool_or_a_non_mapping_is_unsupported(self):
        for params in (
            {**self.PARAMS, "t": True},
            None,
            [],
            ["v", "m", "t", "p"],
            "1.3",
        ):
            with self.subTest(params=params):
                with self.assertRaises(suites.UnsupportedAlgorithm):
                    primitives.assert_account_kdf("argon2id", params)

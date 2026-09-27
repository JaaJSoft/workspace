import uuid

from django.test import SimpleTestCase
from rest_framework import serializers

from workspace.vault.serializers import (
    AccountFinalizeSerializer,
    AccountRotateSerializer,
    VaultCreateSerializer,
    VaultEntryWriteSerializer,
    VaultFolderWriteSerializer,
    VaultTagWriteSerializer,
    VaultUpdateSerializer,
    validate_base64url,
)
from workspace.vault.tests.reference.encoding import to_base64url

VALID_PARAMS = {"v": "1.3", "m": 65536, "t": 3, "p": 2}
OPAQUE = "AAAABBBBCCCCDDDD"


def ciphertext(format_version=2, aead_id=1, iv_len=12):
    """A value in the wire format: a six-byte header, the nonce, then 17 bytes
    standing in for the sealed body and its tag."""
    header = bytes([format_version, aead_id, 1, 0, 1, iv_len])
    return to_base64url(header + bytes(iv_len) + bytes(17))


CIPHERTEXT = ciphertext()


def finalize_payload(**overrides):
    payload = {
        "kdf_algo": "argon2id",
        "kdf_params": dict(VALID_PARAMS),
        "kex_public": OPAQUE,
        "sig_public": OPAQUE,
        "wrapped_kex_priv": CIPHERTEXT,
        "wrapped_sig_priv": CIPHERTEXT,
        "sig_over_kex_pub": OPAQUE,
    }
    payload.update(overrides)
    return payload


class AccountFinalizeSerializerTests(SimpleTestCase):
    def test_accepts_a_complete_payload(self):
        serializer = AccountFinalizeSerializer(data=finalize_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_refuses_a_missing_field(self):
        for name in (
            "kdf_algo",
            "kdf_params",
            "kex_public",
            "sig_public",
            "wrapped_kex_priv",
            "wrapped_sig_priv",
            "sig_over_kex_pub",
        ):
            payload = finalize_payload()
            del payload[name]
            with self.subTest(missing=name):
                self.assertFalse(AccountFinalizeSerializer(data=payload).is_valid())

    def test_refuses_an_empty_opaque_field(self):
        serializer = AccountFinalizeSerializer(
            data=finalize_payload(wrapped_kex_priv="")
        )
        self.assertFalse(serializer.is_valid())

    def test_refuses_an_opaque_field_that_is_not_base64url(self):
        """Stored as submitted, a value that is not base64url only fails at
        unlock, where the user has no recourse and no explanation."""
        for value in ("!!!!", "not base64", "AA!!AA", "===="):
            with self.subTest(value=value):
                serializer = AccountFinalizeSerializer(
                    data=finalize_payload(wrapped_kex_priv=value)
                )
                self.assertFalse(serializer.is_valid())

    def test_refuses_kdf_params_that_are_not_positive_integers(self):
        for params in (
            {"v": "1.3", "m": 0, "t": 3, "p": 2},
            {"v": "1.3", "m": 65536, "t": -1, "p": 2},
            {"v": "1.3", "m": 65536, "t": 3},
            {"v": "1.3", "m": "65536", "t": 3, "p": 2},
            {"v": "1.3", "m": 65536.5, "t": 3, "p": 2},
            {"v": "1.3", "m": True, "t": 3, "p": 2},
            {"m": 65536, "t": 3, "p": 2},
            [],
            "argon2id",
        ):
            with self.subTest(params=params):
                serializer = AccountFinalizeSerializer(
                    data=finalize_payload(kdf_params=params)
                )
                self.assertFalse(serializer.is_valid())

    def test_accepts_kdf_params_above_todays_values(self):
        """Cost parameters must be able to rise without a data migration, so
        the server bounds them by the manifest rather than pinning today's."""
        serializer = AccountFinalizeSerializer(
            data=finalize_payload(kdf_params={"v": "1.3", "m": 262144, "t": 5, "p": 4})
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_refuses_kdf_params_outside_the_manifest_bounds(self):
        """A memory cost no browser can allocate is an account that never
        unlocks again."""
        serializer = AccountFinalizeSerializer(
            data=finalize_payload(kdf_params={**VALID_PARAMS, "m": 4 * 1024 * 1024})
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"kdf_params"})

    def test_refuses_an_unknown_kdf_algo_under_its_own_key(self):
        serializer = AccountFinalizeSerializer(data=finalize_payload(kdf_algo="scrypt"))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"kdf_algo"})

    def test_refuses_a_wrapped_key_whose_header_names_an_unknown_aead(self):
        serializer = AccountFinalizeSerializer(
            data=finalize_payload(wrapped_kex_priv=ciphertext(aead_id=7))
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("wrapped_kex_priv", serializer.errors)

    def test_accepts_a_format_1_wrapped_key(self):
        """A tab opened before the deploy still writes format 1."""
        serializer = AccountFinalizeSerializer(
            data=finalize_payload(wrapped_kex_priv=ciphertext(format_version=1))
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)


class ValidateBase64urlTests(SimpleTestCase):
    def test_accepts_the_empty_string(self):
        """A field that allows blank (an optional encrypted description)
        has already decided the empty string is valid; there is nothing to
        decode."""
        self.assertEqual(validate_base64url(""), "")

    def test_still_refuses_non_base64url_text(self):
        with self.assertRaises(serializers.ValidationError):
            validate_base64url("not base64")


class AccountRotateSerializerTests(SimpleTestCase):
    def test_accepts_the_three_rotatable_fields(self):
        serializer = AccountRotateSerializer(
            data={
                "kdf_algo": "argon2id",
                "kdf_params": dict(VALID_PARAMS),
                "wrapped_kex_priv": CIPHERTEXT,
                "wrapped_sig_priv": CIPHERTEXT,
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_does_not_expose_the_public_keys_or_the_salt(self):
        """Rotation re-wraps the same private keys and rewrites nothing else.
        A field that is not declared cannot be written, whatever the body
        happens to carry."""
        declared = set(AccountRotateSerializer().fields)
        self.assertEqual(
            declared,
            {"kdf_algo", "kdf_params", "wrapped_kex_priv", "wrapped_sig_priv"},
        )


class VaultUpdateSerializerTests(SimpleTestCase):
    def _payload(self, **overrides):
        payload = {
            "encrypted_name": CIPHERTEXT,
            "encrypted_description": "",
            "icon": "lock",
            "color": "primary",
            "is_favorite": False,
            "metadata_sig": OPAQUE,
        }
        payload.update(overrides)
        return payload

    def test_accepts_a_plain_icon_and_colour(self):
        serializer = VaultUpdateSerializer(data=self._payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_trailing_newline_never_reaches_the_column(self):
        """Two layers have to agree for this to hold, and only one of them is
        obvious: CharField trims the value before the pattern ever sees it,
        and the pattern ends in ``\\Z`` so it would refuse the untrimmed form
        too. Turning either off must not silently store the newline."""
        serializer = VaultUpdateSerializer(data=self._payload(icon="lock\n"))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["icon"], "lock")

    def test_refuses_a_newline_inside_the_icon(self):
        serializer = VaultUpdateSerializer(data=self._payload(icon="lo\nck"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("icon", serializer.errors)

    def test_refuses_a_newline_inside_the_colour(self):
        serializer = VaultUpdateSerializer(data=self._payload(color="pri\nmary"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("color", serializer.errors)


class TagColourTests(SimpleTestCase):
    """A tag's colour vocabulary, which is not the vault's.

    Both are plaintext columns covered by ``metadata_sig``, so the alphabet
    each accepts is frozen the day a row is signed: widening it later would
    mean every client re-signing every tag, and the server may not re-sign on
    their behalf.
    """

    def _payload(self, colour):
        return {
            "uuid": str(uuid.uuid4()),
            "vault": str(uuid.uuid4()),
            "encrypted_name": CIPHERTEXT,
            "color": colour,
            "metadata_sig": OPAQUE,
        }

    def test_a_tag_takes_a_colour_from_the_shared_hex_palette(self):
        serializer = VaultTagWriteSerializer(data=self._payload("#22c55e"))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_tag_still_takes_a_role_name(self):
        """The vault's own vocabulary stays valid on a tag: rejecting it
        would turn a widening into a breaking change for no gain."""
        serializer = VaultTagWriteSerializer(data=self._payload("neutral"))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_tag_refuses_anything_that_is_neither(self):
        for colour in ("#22C55E", "#22c55", "red; drop table", "rgb(1,2,3)", ""):
            with self.subTest(colour=colour):
                serializer = VaultTagWriteSerializer(data=self._payload(colour))
                self.assertFalse(serializer.is_valid())

    def test_a_vault_does_not_take_a_hex_colour(self):
        """The icon picker the vault shares with the rest of the application
        works in CSS classes, so a hex there would render as nothing."""
        serializer = VaultUpdateSerializer(
            data={
                "encrypted_name": CIPHERTEXT,
                "encrypted_description": CIPHERTEXT,
                "icon": "lock",
                "color": "#22c55e",
                "metadata_sig": OPAQUE,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("color", serializer.errors)


class CiphertextHeaderTests(SimpleTestCase):
    """Every field carrying a wire-format ciphertext refuses a header no
    client will ever read; the headerless values stay opaque."""

    UNKNOWN_FORMAT = ciphertext(format_version=9)
    HPKE_SUITE = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0, "format": 2}

    def _vault_create(self, **overrides):
        return {
            "uuid": str(uuid.uuid4()),
            "encrypted_name": CIPHERTEXT,
            "encrypted_description": "",
            "icon": "lock",
            "color": "primary",
            "metadata_sig": OPAQUE,
            "wrapped_key": OPAQUE,
            "hpke_suite": dict(self.HPKE_SUITE),
            **overrides,
        }

    def _vault_update(self, **overrides):
        return {
            "encrypted_name": CIPHERTEXT,
            "encrypted_description": CIPHERTEXT,
            "icon": "lock",
            "color": "primary",
            "is_favorite": False,
            "metadata_sig": OPAQUE,
            **overrides,
        }

    def _entry(self, **overrides):
        return {
            "uuid": str(uuid.uuid4()),
            "vault": str(uuid.uuid4()),
            "type": "login",
            "is_favorite": False,
            "encrypted_name": CIPHERTEXT,
            "encrypted_notes": "",
            "fields": {"password": CIPHERTEXT},
            "metadata_sig": OPAQUE,
            **overrides,
        }

    def _folder(self, **overrides):
        return {
            "uuid": str(uuid.uuid4()),
            "vault": str(uuid.uuid4()),
            "encrypted_name": CIPHERTEXT,
            "position": 0,
            "metadata_sig": OPAQUE,
            **overrides,
        }

    def _tag(self, **overrides):
        return {
            "uuid": str(uuid.uuid4()),
            "vault": str(uuid.uuid4()),
            "encrypted_name": CIPHERTEXT,
            "color": "neutral",
            "metadata_sig": OPAQUE,
            **overrides,
        }

    def cases(self):
        return (
            (VaultCreateSerializer, self._vault_create, "encrypted_name"),
            (VaultCreateSerializer, self._vault_create, "encrypted_description"),
            (VaultUpdateSerializer, self._vault_update, "encrypted_name"),
            (VaultUpdateSerializer, self._vault_update, "encrypted_description"),
            (VaultEntryWriteSerializer, self._entry, "encrypted_name"),
            (VaultEntryWriteSerializer, self._entry, "encrypted_notes"),
            (VaultFolderWriteSerializer, self._folder, "encrypted_name"),
            (VaultTagWriteSerializer, self._tag, "encrypted_name"),
        )

    def test_the_well_formed_payloads_pass(self):
        for serializer_class, build, _ in self.cases():
            with self.subTest(serializer=serializer_class.__name__):
                serializer = serializer_class(data=build())
                self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_an_unknown_format_is_refused_on_every_ciphertext_field(self):
        for serializer_class, build, field in self.cases():
            with self.subTest(serializer=serializer_class.__name__, field=field):
                serializer = serializer_class(
                    data=build(**{field: self.UNKNOWN_FORMAT})
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)

    def test_an_entry_field_value_with_an_unknown_format_is_refused(self):
        serializer = VaultEntryWriteSerializer(
            data=self._entry(fields={"password": self.UNKNOWN_FORMAT})
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("fields", serializer.errors)

    def test_the_hpke_wrapped_key_is_not_header_checked(self):
        """An HPKE wrap carries no wire header: its first bytes are the
        encapsulated key's, and reading them as a format would refuse most
        wraps."""
        serializer = VaultCreateSerializer(
            data=self._vault_create(wrapped_key=self.UNKNOWN_FORMAT)
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_both_hpke_suite_formats_are_accepted(self):
        format_1 = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0}
        for suite in (format_1, {**format_1, "format": 2}):
            with self.subTest(suite=suite):
                serializer = VaultCreateSerializer(
                    data=self._vault_create(hpke_suite=suite)
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_an_explicit_format_1_hpke_suite_is_refused(self):
        serializer = VaultCreateSerializer(
            data=self._vault_create(hpke_suite={**self.HPKE_SUITE, "format": 1})
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("hpke_suite", serializer.errors)

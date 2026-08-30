import uuid

from django.test import SimpleTestCase
from rest_framework import serializers

from workspace.vault.serializers import (
    AccountFinalizeSerializer,
    AccountRotateSerializer,
    VaultTagWriteSerializer,
    VaultUpdateSerializer,
    validate_base64url,
)

VALID_PARAMS = {"m": 65536, "t": 3, "p": 2}
OPAQUE = "AAAABBBBCCCCDDDD"


def finalize_payload(**overrides):
    payload = {
        "kdf_algo": "argon2id",
        "kdf_params": dict(VALID_PARAMS),
        "kex_public": OPAQUE,
        "sig_public": OPAQUE,
        "wrapped_kex_priv": OPAQUE,
        "wrapped_sig_priv": OPAQUE,
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
            {"m": 0, "t": 3, "p": 2},
            {"m": 65536, "t": -1, "p": 2},
            {"m": 65536, "t": 3},
            {"m": "65536", "t": 3, "p": 2},
            {"m": 65536.5, "t": 3, "p": 2},
            {"m": True, "t": 3, "p": 2},
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
        the server pins their shape and never their magnitude."""
        serializer = AccountFinalizeSerializer(
            data=finalize_payload(kdf_params={"m": 262144, "t": 5, "p": 4})
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_refuses_an_unknown_kdf_algo(self):
        serializer = AccountFinalizeSerializer(data=finalize_payload(kdf_algo="scrypt"))
        self.assertFalse(serializer.is_valid())


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
                "kdf_params": dict(VALID_PARAMS),
                "wrapped_kex_priv": OPAQUE,
                "wrapped_sig_priv": OPAQUE,
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_does_not_expose_the_public_keys_or_the_salt(self):
        """Rotation re-wraps the same private keys and rewrites nothing else.
        A field that is not declared cannot be written, whatever the body
        happens to carry."""
        declared = set(AccountRotateSerializer().fields)
        self.assertEqual(
            declared, {"kdf_params", "wrapped_kex_priv", "wrapped_sig_priv"}
        )


class VaultUpdateSerializerTests(SimpleTestCase):
    def _payload(self, **overrides):
        payload = {
            "encrypted_name": OPAQUE,
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
            "encrypted_name": OPAQUE,
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
                "encrypted_name": OPAQUE,
                "encrypted_description": OPAQUE,
                "icon": "lock",
                "color": "#22c55e",
                "metadata_sig": OPAQUE,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("color", serializer.errors)

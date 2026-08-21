from django.test import SimpleTestCase

from workspace.vault.serializers import (
    AccountFinalizeSerializer,
    AccountRotateSerializer,
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

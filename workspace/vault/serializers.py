"""Serializers for the account envelope.

Every opaque field is validated on shape alone. The server cannot tell a
wrapped private key from any other base64url text, and it must not pretend
otherwise: what it can enforce is presence, non-emptiness, and that the KDF
parameters are the kind of thing Argon2id can be run with.
"""

from rest_framework import serializers

from .models import AccountIdentity

_KDF_PARAM_KEYS = ("m", "t", "p")


def validate_kdf_params(value):
    """Positive integers for m, t and p - no floor, no ceiling.

    A floor would freeze today's cost parameters into the server, and they
    have to be able to rise without a data migration. A client that lowers
    them harms only its own account, and the browser owns that policy.
    """
    if not isinstance(value, dict):
        raise serializers.ValidationError("kdf_params must be an object")
    missing = [key for key in _KDF_PARAM_KEYS if key not in value]
    if missing:
        raise serializers.ValidationError(f"kdf_params is missing {', '.join(missing)}")
    for key in _KDF_PARAM_KEYS:
        param = value[key]
        # bool is an int in Python, so True would otherwise pass as t=1.
        if isinstance(param, bool) or not isinstance(param, int) or param < 1:
            raise serializers.ValidationError(
                f"kdf_params.{key} must be a positive integer"
            )
    return value


class _OpaqueField(serializers.CharField):
    """base64url text the server stores and can never open."""

    def __init__(self, **kwargs):
        kwargs.setdefault("allow_blank", False)
        kwargs.setdefault("trim_whitespace", False)
        kwargs.setdefault("max_length", 4096)
        super().__init__(**kwargs)


class AccountEnvelopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountIdentity
        fields = [
            "uuid",
            "kdf_algo",
            "kdf_params",
            "kdf_salt",
            "kex_public",
            "sig_public",
            "wrapped_kex_priv",
            "wrapped_sig_priv",
            "sig_over_kex_pub",
            "state",
            "updated_at",
        ]
        read_only_fields = fields


class AccountFinalizeSerializer(serializers.Serializer):
    kdf_algo = serializers.ChoiceField(choices=["argon2id"])
    kdf_params = serializers.JSONField(validators=[validate_kdf_params])
    kex_public = _OpaqueField()
    sig_public = _OpaqueField()
    wrapped_kex_priv = _OpaqueField()
    wrapped_sig_priv = _OpaqueField()
    sig_over_kex_pub = _OpaqueField()


class AccountRotateSerializer(serializers.Serializer):
    """The three fields a password rotation rewrites.

    The public keys and the salt are absent by design: rotation re-wraps the
    same private keys under a key derived from the new password, it never
    mints new ones, and a regenerated salt would orphan the envelope outright.
    """

    kdf_params = serializers.JSONField(validators=[validate_kdf_params])
    wrapped_kex_priv = _OpaqueField()
    wrapped_sig_priv = _OpaqueField()

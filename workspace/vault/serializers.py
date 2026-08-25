"""Serializers for the account envelope and the vault collection.

Every opaque field is validated on shape alone. The server cannot tell a
wrapped private key from any other base64url text, and it must not pretend
otherwise: what it can enforce is presence, non-emptiness, and that the KDF
parameters are the kind of thing Argon2id can be run with.
"""

import re

from rest_framework import serializers

from .models import AccountIdentity, Vault
from .services.attestation import AttestationError, decode_base64url

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


def validate_base64url(value):
    """The one shape the server can check on a value it cannot open.

    Without it, a client bug stores something that is not a ciphertext at all,
    and the account only finds out at unlock time - when there is nothing left
    to do about it. Returns early on a falsy value: a field that allows blank
    (an optional encrypted description) has already decided the empty string
    is a valid value, and there is nothing to decode.
    """
    if not value:
        return value
    try:
        decode_base64url(value)
    except AttestationError as exc:
        raise serializers.ValidationError("must be base64url text") from exc
    return value


class _OpaqueField(serializers.CharField):
    """base64url text the server stores and can never open."""

    def __init__(self, **kwargs):
        kwargs.setdefault("allow_blank", False)
        kwargs.setdefault("trim_whitespace", False)
        kwargs.setdefault("max_length", 4096)
        kwargs.setdefault("validators", [validate_base64url])
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


class AccountInitResponseSerializer(serializers.Serializer):
    """What the browser needs before it can derive anything.

    ``account_uuid`` is the identity row's UUID and the value every
    account-scoped associated data string is bound to; ``kdf_salt`` is the
    only random material the server produces, and it is public.
    """

    account_uuid = serializers.UUIDField()
    kdf_salt = serializers.CharField()


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


# The only HPKE suite v1 defines: DHKEM(X25519, HKDF-SHA256), HKDF-SHA256,
# AES-256-GCM, mode_base. The column exists so a second suite can land without
# a migration; until one does, anything else is a client bug, and storing it
# would produce a wrap nobody can open.
HPKE_SUITE_V1 = {"kem_id": 0x0020, "kdf_id": 0x0001, "aead_id": 0x0002, "mode": 0x00}

_ICON = re.compile(r"^[a-z0-9-]{1,64}$")
_COLOR = re.compile(r"^[a-z0-9-]{1,32}$")


def validate_hpke_suite(value):
    if value != HPKE_SUITE_V1:
        raise serializers.ValidationError("unsupported HPKE suite")
    return value


class VaultSerializer(serializers.ModelSerializer):
    """What the browser needs to open and verify a vault.

    ``owner_account_uuid`` is the owner's AccountIdentity UUID, not a user id:
    it is what the signed payload binds, and auth.User's integer primary key is
    enumerable and reassignable after a deletion.
    """

    owner_account_uuid = serializers.SerializerMethodField()
    wrapped_key = serializers.SerializerMethodField()
    hpke_suite = serializers.SerializerMethodField()

    class Meta:
        model = Vault
        fields = [
            "uuid",
            "owner_account_uuid",
            "encrypted_name",
            "encrypted_description",
            "icon",
            "color",
            "key_version",
            "is_favorite",
            "metadata_sig",
            "wrapped_key",
            "hpke_suite",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_owner_account_uuid(self, vault):
        identity = getattr(vault.owner, "vault_identity", None)
        return str(identity.uuid) if identity else None

    def _own_wrap(self, vault):
        # Populated by the view's Prefetch; the fallback keeps the serializer
        # usable from a test or a shell without silently issuing a query per
        # vault in the listing.
        wraps = getattr(vault, "own_wraps", None)
        if wraps is None:
            return vault.key_wraps.filter(
                recipient=self.context["request"].user
            ).first()
        return wraps[0] if wraps else None

    def get_wrapped_key(self, vault):
        wrap = self._own_wrap(vault)
        return wrap.wrapped_key if wrap else None

    def get_hpke_suite(self, vault):
        wrap = self._own_wrap(vault)
        return wrap.hpke_suite if wrap else None


class VaultCreateSerializer(serializers.Serializer):
    """The client mints the vault UUID: the HPKE info string binds it before
    the request is built, so the server cannot be the one to choose it."""

    uuid = serializers.UUIDField()
    encrypted_name = _OpaqueField()
    encrypted_description = _OpaqueField(allow_blank=True)
    icon = serializers.RegexField(_ICON)
    color = serializers.RegexField(_COLOR)
    metadata_sig = _OpaqueField()
    wrapped_key = _OpaqueField()
    hpke_suite = serializers.JSONField(validators=[validate_hpke_suite])


class VaultUpdateSerializer(serializers.Serializer):
    """Every signed field, always. A rename re-signs the whole payload, so a
    partial write would leave the row carrying a signature over values it no
    longer holds."""

    encrypted_name = _OpaqueField()
    encrypted_description = _OpaqueField(allow_blank=True)
    icon = serializers.RegexField(_ICON)
    color = serializers.RegexField(_COLOR)
    is_favorite = serializers.BooleanField()
    metadata_sig = _OpaqueField()

"""Serializers for the account envelope and the vault collection.

Every opaque field is validated on shape alone. The server cannot tell a
wrapped private key from any other base64url text, and it must not pretend
otherwise: what it can enforce is presence, non-emptiness, and that the KDF
parameters are the kind of thing Argon2id can be run with.
"""

import re

from rest_framework import serializers

from .models import (
    AccountIdentity,
    EntryField,
    EntryType,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultTag,
)
from .services.attestation import AttestationError, decode_base64url
from .services.fields import qualify_field_id

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


_OPAQUE_MAX_LENGTH = 4096


class _OpaqueField(serializers.CharField):
    """base64url text the server stores and can never open."""

    def __init__(self, **kwargs):
        kwargs.setdefault("allow_blank", False)
        kwargs.setdefault("trim_whitespace", False)
        kwargs.setdefault("max_length", _OPAQUE_MAX_LENGTH)
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

# The trailing anchor is \Z, not $: $ also matches before a final newline,
# and RegexField validates with search(), so an icon ending in one would
# pass a pattern that looks closed.
_ICON = re.compile(r"^[a-z0-9-]{1,64}\Z")
_COLOR = re.compile(r"^[a-z0-9-]{1,32}\Z")


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


class VaultFolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = VaultFolder
        fields = [
            "uuid",
            "vault",
            "parent",
            "encrypted_name",
            "position",
            "metadata_sig",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class VaultFolderWriteSerializer(serializers.Serializer):
    """Every signed field, always - a partial write would leave the row
    carrying a signature over values it no longer holds."""

    uuid = serializers.UUIDField()
    vault = serializers.UUIDField()
    parent = serializers.UUIDField(allow_null=True, required=False, default=None)
    encrypted_name = _OpaqueField()
    # Capped rather than unbounded: it enters a PositiveIntegerField, and a
    # value above its range is a 500 from the database rather than a 400 here.
    position = serializers.IntegerField(min_value=0, max_value=100_000)
    metadata_sig = _OpaqueField()


class VaultTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = VaultTag
        fields = [
            "uuid",
            "vault",
            "encrypted_name",
            "color",
            "metadata_sig",
            "created_at",
        ]
        read_only_fields = fields


class VaultTagWriteSerializer(serializers.Serializer):
    """Every signed field, always - a partial write would leave the row
    carrying a signature over values it no longer holds."""

    uuid = serializers.UUIDField()
    vault = serializers.UUIDField()
    encrypted_name = _OpaqueField()
    color = serializers.RegexField(_COLOR)
    metadata_sig = _OpaqueField()


class EntryFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = EntryField
        fields = ["field_id", "encrypted_value"]
        read_only_fields = fields


class VaultEntrySerializer(serializers.ModelSerializer):
    # Named entry_fields on the wire: `fields` is Meta's own attribute name on
    # a ModelSerializer, so the declared field cannot carry it.
    entry_fields = EntryFieldSerializer(source="fields", many=True, read_only=True)
    tags = serializers.SerializerMethodField()

    class Meta:
        model = VaultEntry
        fields = [
            "uuid",
            "vault",
            "type",
            "folder",
            "tags",
            "is_favorite",
            "encrypted_name",
            "encrypted_notes",
            "key_version",
            "entry_version",
            "metadata_sig",
            "deleted_at",
            "last_used_at",
            "created_at",
            "updated_at",
            "entry_fields",
        ]
        read_only_fields = fields

    def get_tags(self, entry):
        # Sorted so the client can rebuild the signed payload from the response
        # without re-sorting - and so two reads of one entry are byte-identical.
        return sorted(str(tag.uuid) for tag in entry.tags.all())


def validate_field_map(value):
    """A mapping of stored field id to ciphertext, catalogue-checked."""
    if not isinstance(value, dict):
        raise serializers.ValidationError("fields must be an object")
    if len(value) > 64:
        raise serializers.ValidationError("an entry carries at most 64 fields")
    for field_id, ciphertext in value.items():
        try:
            qualify_field_id(field_id)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if not isinstance(ciphertext, str) or not ciphertext:
            raise serializers.ValidationError("a field value must be base64url text")
        # The same cap _OpaqueField applies: these values ride inside a JSON
        # object rather than as serializer fields, and must not escape it by
        # doing so.
        if len(ciphertext) > _OPAQUE_MAX_LENGTH:
            raise serializers.ValidationError("a field value is too long")
        validate_base64url(ciphertext)
    return value


class VaultEntryWriteSerializer(serializers.Serializer):
    """Every signed field, always.

    key_version and entry_version are absent: both are the server's at
    creation and the row's on update. They are still inside the signature, so
    a client that signed anything else fails verification rather than writing
    a row it cannot re-verify.
    """

    uuid = serializers.UUIDField()
    vault = serializers.UUIDField()
    type = serializers.ChoiceField(choices=EntryType.choices)
    folder = serializers.UUIDField(allow_null=True, required=False, default=None)
    tags = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, max_length=64, default=list
    )
    is_favorite = serializers.BooleanField()
    encrypted_name = _OpaqueField()
    encrypted_notes = _OpaqueField(allow_blank=True)
    fields = serializers.JSONField(validators=[validate_field_map])
    metadata_sig = _OpaqueField()


class FolderDeleteEntrySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    metadata_sig = _OpaqueField()


class FolderDeleteSerializer(serializers.Serializer):
    """The folder's entries, re-signed with no folder.

    Capped rather than paginated: a folder with 500 entries is a UI problem,
    and a silent truncation here would delete a folder while leaving entries
    in it.
    """

    entries = serializers.ListField(
        child=FolderDeleteEntrySerializer(), allow_empty=True, max_length=500
    )

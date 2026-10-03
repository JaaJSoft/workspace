"""Serializers for the account envelope and the vault collection.

Every opaque field is validated on shape alone. The server cannot tell a
wrapped private key from any other base64url text, and it must not pretend
otherwise: what it can enforce is presence, non-emptiness, and that the KDF
algorithm and parameters are ones the suite manifest declares. It also refuses
a ciphertext whose header names a format or AEAD it does not know, or a nonce
length that AEAD does not declare.
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
from .services import suites
from .services.attestation import AttestationError, decode_base64url
from .services.fields import qualify_field_id


class _AccountKdfMixin:
    """The algorithm and its parameters are checked together: the bounds a
    parameter must fall in belong to the algorithm the manifest declares.

    The bounds are the manifest's, not today's values, so the cost can rise
    without a data migration - and a cost no browser can allocate is refused
    before it becomes an account that never unlocks again.
    """

    def validate(self, attrs):
        try:
            suites.check_kdf_algo(attrs["kdf_algo"])
        except ValueError as exc:
            raise serializers.ValidationError(
                {"kdf_algo": ["unsupported kdf_algo"]}
            ) from exc
        try:
            suites.check_account_kdf(attrs["kdf_algo"], attrs["kdf_params"])
        except ValueError as exc:
            raise serializers.ValidationError(
                {"kdf_params": ["unsupported kdf_params"]}
            ) from exc
        return attrs


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


def validate_ciphertext(value):
    """base64url, then a wire header this server knows how to store."""
    if not value:
        return value
    validate_base64url(value)
    try:
        suites.check_ciphertext(decode_base64url(value))
    except suites.UnsupportedCiphertext as exc:
        raise serializers.ValidationError("unsupported ciphertext header") from exc
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


class _CiphertextField(_OpaqueField):
    """A value sealed in the wire format: the server cannot open it, but it can
    refuse a header no client will ever read."""

    def __init__(self, **kwargs):
        kwargs.setdefault("validators", [validate_ciphertext])
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


class AccountFinalizeSerializer(_AccountKdfMixin, serializers.Serializer):
    # The manifest decides which algorithms exist, not a choice list here.
    kdf_algo = serializers.CharField(max_length=16)
    kdf_params = serializers.JSONField()
    kex_public = _OpaqueField()
    sig_public = _OpaqueField()
    wrapped_kex_priv = _CiphertextField()
    wrapped_sig_priv = _CiphertextField()
    sig_over_kex_pub = _OpaqueField()


class AccountRotateSerializer(_AccountKdfMixin, serializers.Serializer):
    """The four fields a password rotation rewrites.

    The public keys and the salt are absent by design: rotation re-wraps the
    same private keys under a key derived from the new password, it never
    mints new ones, and a regenerated salt would orphan the envelope outright.
    ``kdf_algo`` travels with ``kdf_params`` because the two describe one
    derivation.
    """

    kdf_algo = serializers.CharField(max_length=16)
    kdf_params = serializers.JSONField()
    wrapped_kex_priv = _CiphertextField()
    wrapped_sig_priv = _CiphertextField()


# The trailing anchor is \Z, not $: $ also matches before a final newline,
# and RegexField validates with search(), so an icon ending in one would
# pass a pattern that looks closed.
_ICON = re.compile(r"^[a-z0-9-]{1,64}\Z")
# A vault's colour is a daisyUI role name, because the icon picker it shares
# with the rest of the application works in CSS classes.
_COLOR = re.compile(r"^[a-z0-9-]{1,32}\Z")
# A tag's is the shared hex palette every other tag in the application
# already uses, so <tag-chip> and its colour picker are reusable as they
# are. Kept apart from the vault's rather than widened into it: one alphabet
# per vocabulary, and both are covered by metadata_sig - the day a row is
# signed, changing either means every client re-signing.
_TAG_COLOR = re.compile(r"^(#[0-9a-f]{6}|[a-z0-9-]{1,32})\Z")


def validate_hpke_suite(value):
    """A suite the manifest declares, spelled the way its format spells it.
    Storing any other would produce a wrap nobody can open."""
    try:
        suites.check_hpke_suite(value)
    except ValueError as exc:
        raise serializers.ValidationError("unsupported HPKE suite") from exc
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
    # Annotated by the listing. The default keeps a serializer used from a
    # shell or a single-vault read from raising on a queryset that did not
    # annotate.
    entry_count = serializers.IntegerField(read_only=True, default=0)

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
            "entry_count",
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
    encrypted_name = _CiphertextField()
    encrypted_description = _CiphertextField(allow_blank=True)
    icon = serializers.RegexField(_ICON)
    color = serializers.RegexField(_COLOR)
    metadata_sig = _OpaqueField()
    # HPKE output, headerless: its agility lives in hpke_suite.
    wrapped_key = _OpaqueField()
    hpke_suite = serializers.JSONField(validators=[validate_hpke_suite])


class VaultUpdateSerializer(serializers.Serializer):
    """Every signed field, always. A rename re-signs the whole payload, so a
    partial write would leave the row carrying a signature over values it no
    longer holds."""

    encrypted_name = _CiphertextField()
    encrypted_description = _CiphertextField(allow_blank=True)
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
    encrypted_name = _CiphertextField()
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
    encrypted_name = _CiphertextField()
    color = serializers.RegexField(_TAG_COLOR)
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
            # Fixed wording rather than the exception's: a field id is a value
            # the caller chose, and it must not travel back out inside an error.
            raise serializers.ValidationError(
                "a field id must be a reserved identifier or a well-formed "
                "custom: label"
            ) from exc
        if not isinstance(ciphertext, str) or not ciphertext:
            raise serializers.ValidationError("a field value must be base64url text")
        # The same cap _OpaqueField applies: these values ride inside a JSON
        # object rather than as serializer fields, and must not escape it by
        # doing so.
        if len(ciphertext) > _OPAQUE_MAX_LENGTH:
            raise serializers.ValidationError("a field value is too long")
        validate_ciphertext(ciphertext)
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
    encrypted_name = _CiphertextField()
    encrypted_notes = _CiphertextField(allow_blank=True)
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


MAX_ITEMS = 200
MAX_CIPHERTEXTS = 2000

ITEM_KEYS = {
    "metadata": {
        "kind",
        "encrypted_name",
        "encrypted_description",
        "metadata_sig",
        "expected_sig",
    },
    "wrap": {"kind", "wrapped_key", "hpke_suite", "wrapped_key_expected"},
    "folder": {"kind", "uuid", "encrypted_name", "metadata_sig", "expected_sig"},
    "tag": {"kind", "uuid", "encrypted_name", "metadata_sig", "expected_sig"},
    "entry": {
        "kind",
        "uuid",
        "encrypted_name",
        "encrypted_notes",
        "fields",
        "metadata_sig",
        "expected_sig",
    },
}


class MigrateItemSerializer(serializers.Serializer):
    """One rewrite. The key set is exact per kind: a plaintext column in the
    body is refused rather than ignored, so a migration can never carry a
    change the signature was not meant to cover."""

    kind = serializers.ChoiceField(choices=sorted(ITEM_KEYS))
    uuid = serializers.UUIDField(required=False)
    encrypted_name = _CiphertextField(required=False)
    encrypted_description = _CiphertextField(required=False, allow_blank=True)
    encrypted_notes = _CiphertextField(required=False, allow_blank=True)
    fields = serializers.JSONField(required=False, validators=[validate_field_map])
    metadata_sig = _OpaqueField(required=False)
    expected_sig = _OpaqueField(required=False)
    wrapped_key = _OpaqueField(required=False)
    wrapped_key_expected = _OpaqueField(required=False)
    hpke_suite = serializers.JSONField(required=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or data.get("kind") not in ITEM_KEYS:
            raise serializers.ValidationError("unknown kind")
        if set(data) != ITEM_KEYS[data["kind"]]:
            raise serializers.ValidationError("wrong key set for this kind")
        return super().to_internal_value(data)


class MigrateBatchSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=MigrateItemSerializer(), min_length=1, max_length=MAX_ITEMS
    )

    def validate_items(self, items):
        seen = set()
        ciphertexts = 0
        for item in items:
            key = (item["kind"], item.get("uuid"))
            if key in seen:
                raise serializers.ValidationError("an item appears twice")
            seen.add(key)
            ciphertexts += sum(
                1
                for name in (
                    "encrypted_name",
                    "encrypted_description",
                    "encrypted_notes",
                )
                if item.get(name)
            )
            ciphertexts += len(item.get("fields", {}))
        if ciphertexts > MAX_CIPHERTEXTS:
            raise serializers.ValidationError("too many ciphertexts in one batch")
        return items

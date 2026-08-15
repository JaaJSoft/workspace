from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class AccountIdentity(models.Model):
    """Per-user cryptographic identity: one KDF envelope, one key-exchange
    pair, one signature pair.

    Every opaque field holds base64url text produced by the browser. The
    server stores and returns them; it can never open them. ``kdf_algo`` and
    ``kdf_params`` are what make a future parameter change possible without
    a data migration - never read them as constants.
    """

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vault_identity",
    )
    kdf_algo = models.CharField(max_length=16, default="argon2id")
    kdf_params = models.JSONField(default=dict)
    # 32 server-generated random bytes; the only crypto material the server
    # produces, and it is public.
    kdf_salt = models.TextField()
    # Public keys and signatures carry a one-byte algorithm prefix (§3.2).
    kex_public = models.TextField()
    sig_public = models.TextField()
    wrapped_kex_priv = models.TextField()
    wrapped_sig_priv = models.TextField()
    sig_over_kex_pub = models.TextField()
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "account identities"

    def __str__(self):
        return f"Vault identity of {self.user_id}"


class VaultRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MEMBER = "member", "Member"


class Vault(models.Model):
    """A container of entries, sealed under a single symmetric vault key.

    ``metadata_sig`` signs the canonical CBOR of the vault metadata, so a
    server that rewrites ``encrypted_name`` or ``icon`` is detected by the
    client. ``key_version`` is bumped by a vault key rotation and is what
    lets an old ciphertext name the wrap it needs.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vaults",
    )
    encrypted_name = models.TextField()
    encrypted_description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=64, default="lock")
    color = models.CharField(max_length=32, default="primary")
    key_version = models.PositiveIntegerField(default=1)
    # Deliberate departure from §9 of the crypto norm, documented in the
    # design spec: URIs are encrypted, so autofill matching happens client
    # side and the server cannot profile which sites a user holds.
    encrypt_uris = models.BooleanField(default=True)
    metadata_sig = models.TextField()
    is_favorite = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["owner", "created_at"]),
        ]
        constraints = [
            # An unsigned vault is what a hostile server would insert. Django
            # only enforces blank=False through full_clean(), which nothing
            # calls on its own, so the guarantee has to live in the database.
            models.CheckConstraint(
                condition=~models.Q(metadata_sig=""),
                name="vault_metadata_sig_not_empty",
            ),
        ]

    def __str__(self):
        return f"Vault {self.uuid}"


class VaultKeyWrap(models.Model):
    """The vault key sealed to one recipient with HPKE.

    ``hpke_suite`` records the suite that produced ``wrapped_key`` so the
    suite can change without a data migration; never assume the v1 values.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    vault = models.ForeignKey(
        Vault,
        on_delete=models.CASCADE,
        related_name="key_wraps",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vault_key_wraps",
    )
    # Native HPKE output (enc || ciphertext); not the §3.3 wire format.
    wrapped_key = models.TextField()
    key_version = models.PositiveIntegerField(default=1)
    hpke_suite = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vault", "recipient"],
                name="unique_vault_key_wrap_per_recipient",
            ),
        ]
        indexes = [
            models.Index(fields=["recipient"]),
        ]

    def __str__(self):
        return f"Key wrap of {self.vault_id} for {self.recipient_id}"


class VaultFolder(models.Model):
    """A folder inside one vault. The tree shape is plaintext; only the name
    is encrypted.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    vault = models.ForeignKey(
        Vault,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    encrypted_name = models.TextField()
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["vault", "parent"]),
        ]

    def __str__(self):
        return f"Folder {self.uuid}"

    def clean(self):
        """Reject a cross-vault parent and any parent cycle.

        Neither is expressible as a database constraint without a recursive
        trigger, so callers must run ``full_clean()`` before saving.
        """
        if self.parent_id is None:
            return
        if self.parent.vault_id != self.vault_id:
            raise ValidationError({"parent": "Parent folder belongs to another vault."})
        seen = {self.pk}
        ancestor = self.parent
        while ancestor is not None:
            if ancestor.pk in seen:
                raise ValidationError({"parent": "Folder cannot contain itself."})
            seen.add(ancestor.pk)
            ancestor = ancestor.parent


class VaultTag(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    vault = models.ForeignKey(
        Vault,
        on_delete=models.CASCADE,
        related_name="tags",
    )
    encrypted_name = models.TextField()
    color = models.CharField(max_length=32, default="neutral")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Tag {self.uuid}"


class EntryType(models.TextChoices):
    LOGIN = "login", "Login"


class VaultEntry(models.Model):
    """One secret, stored flat.

    The table is deliberately not split per type: child tables would hold
    nothing but opaque text, the table an entry sits in cannot be encrypted,
    and the main query would turn polymorphic on the second type. Type
    specialisation lives in Python proxies over ``type`` instead.

    ``key_version`` names the vault key generation that derived the entry
    key; ``entry_version`` versions the content schema. They move for
    unrelated reasons - never collapse them into one column.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    vault = models.ForeignKey(
        Vault,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    type = models.CharField(
        max_length=32, choices=EntryType.choices, default=EntryType.LOGIN
    )
    # RESTRICT, not SET_NULL: folder_id is plaintext but signed (§3.5), so a
    # database-side unlink would break metadata_sig and the client would read a
    # legitimate folder deletion as tampering. Not PROTECT either - that would
    # also refuse a whole-vault deletion, where the entries go with the vault.
    folder = models.ForeignKey(
        VaultFolder,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="entries",
    )
    tags = models.ManyToManyField(VaultTag, blank=True, related_name="entries")
    is_favorite = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    encrypted_name = models.TextField()
    encrypted_notes = models.TextField(blank=True, default="")
    key_version = models.PositiveIntegerField(default=1)
    entry_version = models.PositiveIntegerField(default=1)
    metadata_sig = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "vault entries"
        indexes = [
            models.Index(fields=["vault", "deleted_at"]),
            models.Index(fields=["vault", "folder"]),
            models.Index(fields=["vault", "is_favorite"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(metadata_sig=""),
                name="entry_metadata_sig_not_empty",
            ),
        ]

    def __str__(self):
        return f"Entry {self.uuid}"

    def clean(self):
        """Reject a folder belonging to another vault.

        Not expressible as a database constraint, so callers must run
        ``full_clean()`` before saving. The ``tags`` M2M carries the same
        risk and cannot be checked here - Django validates a many-to-many
        only after the row exists - so the API layer owns that one.
        """
        if self.folder_id is None:
            return
        if self.folder.vault_id != self.vault_id:
            raise ValidationError({"folder": "Folder belongs to another vault."})


class EntryField(models.Model):
    """One encrypted field of an entry.

    ``field_id`` is bound into the AEAD associated data, which is what stops
    a ciphertext from being moved between two fields of the same entry. The
    reserved-identifier catalogue is enforced by the type registry, not here:
    a new entry type must be able to declare its own without a migration.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    entry = models.ForeignKey(
        VaultEntry,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    field_id = models.CharField(max_length=64)
    encrypted_value = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "field_id"],
                name="unique_entry_field_id",
            ),
            # "name" and "notes" derive the same AEAD associated data as
            # VaultEntry.encrypted_name/encrypted_notes (design spec
            # §3.4), which live in another table and so escape the unique
            # constraint above - a permutation between the two would still
            # pass AEAD verification.
            models.CheckConstraint(
                condition=~models.Q(field_id__in=("name", "notes")),
                name="entry_field_id_not_reserved",
            ),
        ]

    def __str__(self):
        return f"{self.field_id} of {self.entry_id}"

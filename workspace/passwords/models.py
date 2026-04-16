from django.conf import settings
from django.db import models


from workspace.common.uuids import uuid_v7_or_v4


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

class Vault(models.Model):
    """A password vault belonging to a user.

    A user may own multiple vaults (e.g. Personal, Work).  Each vault has its
    own master-password-derived key so that vaults are independently locked.

    Zero-knowledge design
    ─────────────────────
    The server never sees the plaintext master password or the derived
    encryption key.  The client (browser) performs key derivation:

        master_password
            └─ PBKDF2-SHA256 (kdf_iterations, kdf_salt)  → stretched_key
                └─ HKDF  → encryption_key  +  mac_key
                    └─ AES-256-GCM decrypt  → vault_key  (stored as protected_vault_key)

    The server only stores ``master_password_hash``, which is a bcrypt hash
    of a second PBKDF2 pass performed client-side:
        client sends  PBKDF2(stretched_key, master_password, 1)
        server stores bcrypt(that value)
    This lets the server verify the master password without knowing it.
    """

    class KdfAlgorithm(models.TextChoices):
        PBKDF2_SHA256 = 'pbkdf2_sha256', 'PBKDF2-SHA256'
        ARGON2ID = 'argon2id', 'Argon2id'

    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='vaults')
    name = models.CharField(max_length=100, default='Personal')
    description = models.TextField(blank=True, default='')
    icon = models.CharField(max_length=50, blank=True, default='vault')
    color = models.CharField(max_length=50, blank=True, default='primary')

    # Master password verification (server-side, one-way)
    master_password_hash = models.CharField(max_length=255, blank=True, default='')

    # Protected vault key: the randomly-generated AES-256-GCM vault key,
    # encrypted client-side with the stretched master key. Opaque to server.
    protected_vault_key = models.TextField(blank=True, default='')

    # Key derivation parameters – sent to client so it can reproduce the key
    kdf_algorithm = models.CharField(
        max_length=20, choices=KdfAlgorithm.choices, default=KdfAlgorithm.PBKDF2_SHA256
    )
    kdf_iterations = models.PositiveIntegerField(default=600_000)
    kdf_salt = models.CharField(max_length=44, blank=True, default='')  # 32-byte base64url

    # Argon2id-only parameters (null when algorithm is PBKDF2)
    kdf_memory = models.PositiveIntegerField(null=True, blank=True)       # KiB
    kdf_parallelism = models.PositiveIntegerField(null=True, blank=True)

    is_setup = models.BooleanField(default=False)  # True once master password is configured

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['user'], name='vault_user_idx'),
        ]

    def __str__(self):
        return f'{self.user} / {self.name}'


# ---------------------------------------------------------------------------
# Folder
# ---------------------------------------------------------------------------

class PasswordFolder(models.Model):
    """Hierarchical folder for grouping password entries within a vault."""

    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    vault = models.ForeignKey(Vault, on_delete=models.CASCADE, related_name='folders')
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children'
    )
    name = models.CharField(max_length=255)
    icon = models.CharField(max_length=50, blank=True, default='folder')
    color = models.CharField(max_length=50, blank=True, default='')
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        indexes = [
            models.Index(fields=['vault'], name='folder_vault_idx'),
            models.Index(fields=['parent'], name='folder_parent_idx'),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Tag
# ---------------------------------------------------------------------------

class PasswordTag(models.Model):
    """Flat label that can be applied to entries across folders."""

    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    vault = models.ForeignKey(Vault, on_delete=models.CASCADE, related_name='tags')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=50, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['vault', 'name'], name='tag_vault_name_uniq'),
        ]
        indexes = [
            models.Index(fields=['vault'], name='tag_vault_idx'),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Entry (base)  (multi-table inheritance)
# ---------------------------------------------------------------------------

def entry_icon_upload_path(instance, filename):
    return f'passwords/vaults/{instance.vault_id}/icons/{instance.uuid}/{filename}'


class PasswordEntry(models.Model):
    """Base model shared by all entry types.

    Uses Django multi-table inheritance: each concrete subclass (LoginEntry, …)
    gets its own DB table linked back here by a OneToOne on the UUID primary key.

    Encrypted name
    ──────────────
    ``encrypted_name`` is the only encrypted field on the base model.  It is
    AES-256-GCM ciphertext produced by the browser so the server never learns
    the entry title.  The plaintext ``type`` field lets the server filter
    entries by kind without decrypting anything.

    Icons / visual identity
    ───────────────────────
    ``icon`` is a Lucide icon name (not sensitive, plaintext).
    ``icon_color`` is a DaisyUI/Tailwind colour class.
    ``custom_icon`` is an optional user-uploaded image (favicon, logo …).
    The server stores these as-is; they are deliberately not zero-knowledge
    because they are cosmetic, not secret.
    """

    class EntryType(models.TextChoices):
        LOGIN = 'login', 'Login'

    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    vault = models.ForeignKey(Vault, on_delete=models.CASCADE, related_name='entries')
    folder = models.ForeignKey(
        PasswordFolder, on_delete=models.SET_NULL, null=True, blank=True, related_name='entries'
    )
    tags = models.ManyToManyField(
        PasswordTag, through='PasswordEntryTag', blank=True, related_name='entries'
    )

    # Discriminator – kept in plaintext for server-side filtering without decryption
    type = models.CharField(
        max_length=20, choices=EntryType.choices, default=EntryType.LOGIN, db_index=True
    )

    # Entry title, encrypted client-side (AES-256-GCM, same vault key)
    encrypted_name = models.TextField()

    # Visual identity (not sensitive – cosmetic only)
    icon = models.CharField(max_length=50, blank=True, default='key-round')
    icon_color = models.CharField(max_length=50, blank=True, default='')
    custom_icon = models.ImageField(
        upload_to=entry_icon_upload_path, null=True, blank=True
    )

    is_favorite = models.BooleanField(default=False, db_index=True)

    # Soft-delete – entries move to trash before permanent removal
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Tracks last copy/view for "recently used" sorting (lightweight, no audit overhead)
    last_used_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['vault', 'type'], name='entry_vault_type_idx'),
            models.Index(fields=['vault', 'deleted_at'], name='entry_vault_deleted_idx'),
            models.Index(fields=['vault', 'is_favorite'], name='entry_vault_fav_idx'),
        ]

    def __str__(self):
        return f'{self.type} entry {self.uuid}'


class PasswordEntryTag(models.Model):
    """Explicit through-table for the Entry ↔ Tag many-to-many."""

    entry = models.ForeignKey(PasswordEntry, on_delete=models.CASCADE)
    tag = models.ForeignKey(PasswordTag, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['entry', 'tag'], name='entry_tag_uniq'),
        ]


# ── Login ────────────────────────────────────────────────────────────────────

class LoginEntry(PasswordEntry):
    """Website or app credentials.

    ``uris`` stores a JSON array of ``{uri, match_type}`` objects
    (e.g. ``[{"uri": "https://github.com", "match": "domain"}]``) in plaintext
    so the server can support domain-based autofill lookups.
    """

    encrypted_username = models.TextField(blank=True, default='')
    encrypted_password = models.TextField(blank=True, default='')
    encrypted_totp_secret = models.TextField(blank=True, default='')
    uris = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Login entry'
        verbose_name_plural = 'Login entries'
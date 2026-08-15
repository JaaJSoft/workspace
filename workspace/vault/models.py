from django.conf import settings
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
    state = models.CharField(max_length=7, choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "account identities"

    def __str__(self):
        return f"Vault identity of {self.user_id}"

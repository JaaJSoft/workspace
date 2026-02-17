from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class UserPresence(models.Model):
    """Tracks the last activity timestamp for each user (presence system)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='presence',
    )
    last_seen = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = 'User presence'
        verbose_name_plural = 'User presences'

    def __str__(self):
        return f'{self.user} — last seen {self.last_seen}'


class UserSetting(models.Model):
    """Key-value store for per-user, per-module settings.

    Any module can read/write settings by specifying its own ``module``
    namespace (e.g. ``"core"``, ``"files"``, ``"notes"``).
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='settings',
    )
    module = models.CharField(max_length=64, db_index=True)
    key = models.CharField(max_length=128)
    value = models.JSONField(default=None, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'module', 'key'],
                name='unique_user_module_key',
            ),
        ]
        ordering = ['module', 'key']

    def __str__(self):
        return f'{self.user} / {self.module}.{self.key}'

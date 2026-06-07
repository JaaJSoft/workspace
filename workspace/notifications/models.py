from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class Notification(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    origin = models.CharField(max_length=50)
    icon = models.CharField(max_length=50)
    color = models.CharField(
        max_length=20, blank=True, default=""
    )  # DaisyUI color: 'primary', 'accent', 'success', ...
    priority = models.CharField(
        max_length=6, choices=Priority.choices, default=Priority.NORMAL
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    url = models.CharField(max_length=500, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            # Partial index for the unread badge query
            # `filter(recipient=u, read_at__isnull=True).count()`. Excluding
            # read rows keeps the index small (badge accuracy matters far
            # more than read-history queries, which never filter on read_at
            # in isolation — see workspace/notifications/views.py).
            models.Index(
                fields=["recipient"],
                name="notif_unread_partial",
                condition=models.Q(read_at__isnull=True),
            ),
            models.Index(
                fields=["recipient", "origin", "read_at"], name="notif_rcpt_origin_read"
            ),
        ]

    def __str__(self):
        return f"{self.title} -> {self.recipient}"


class PushSubscription(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PushSubscription({self.user.username}, {self.endpoint[:40]}...)"

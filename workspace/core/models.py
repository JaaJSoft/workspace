from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class EmailDelivery(models.Model):
    """One mail the instance tried to send on its own behalf.

    Answers "why did this person get this mail" (``feature``, ``template``,
    ``user``) and "why did it not arrive" (``status``, ``error``). The body is
    deliberately not kept: a password reset or a verification mail carries a
    working credential, and this table outlives it.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        BOUNCED = "bounced", "Bounced"
        SUPPRESSED = "suppressed", "Suppressed"
        RATE_LIMITED = "rate_limited", "Rate limited"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    # Normalized (see core.services.email.normalize_address), so rate limits
    # and suppressions match however a caller spelled the address.
    to_address = models.EmailField(max_length=254)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    feature = models.CharField(max_length=64)
    template = models.CharField(max_length=200)
    subject = models.CharField(max_length=255)
    transactional = models.BooleanField(default=True)
    message_id = models.CharField(max_length=255, blank=True, default="")
    # The id the provider gave the mail when it went out through an anymail
    # backend; its bounce reports name the mail by this one, not by ours.
    provider_message_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "email deliveries"
        indexes = [
            # Per-recipient rate limit and "what did this address get".
            models.Index(fields=["to_address", "created_at"]),
            # Global rate limit.
            models.Index(fields=["created_at"]),
            # Latest admin test mail, per feature.
            models.Index(fields=["feature", "created_at"]),
        ]

    def __str__(self):
        return f"{self.feature} to {self.to_address} ({self.status})"


class EmailSuppression(models.Model):
    """An address the instance must not mail again.

    An empty ``feature`` blocks every mail to the address - a hard bounce or a
    complaint says the mailbox or its owner refuses us. An unsubscribe blocks
    the one feature it came from; transactional mail still goes through.
    """

    class Reason(models.TextChoices):
        HARD_BOUNCE = "hard_bounce", "Hard bounce"
        COMPLAINT = "complaint", "Complaint"
        UNSUBSCRIBED = "unsubscribed", "Unsubscribed"
        MANUAL = "manual", "Manual"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    address = models.EmailField(max_length=254)
    feature = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=16, choices=Reason.choices)
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["address", "feature"], name="core_email_suppression_unique"
            ),
        ]

    def __str__(self):
        scope = self.feature or "all mail"
        return f"{self.address} ({scope}, {self.reason})"

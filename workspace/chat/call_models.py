from django.conf import settings
from django.db import models
from django.db.models import Q

from workspace.common.uuids import uuid_v7_or_v4


class Call(models.Model):
    class Status(models.TextChoices):
        RINGING = 'ringing', 'Ringing'
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'Ended'
        MISSED = 'missed', 'Missed'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        'chat.Conversation',
        on_delete=models.CASCADE,
        related_name='calls',
    )
    initiator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='initiated_calls',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RINGING)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['conversation'],
                condition=Q(status__in=['ringing', 'active']),
                name='one_active_call_per_conversation',
            ),
        ]
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
        ]

    def __str__(self):
        return f'Call {self.uuid} ({self.status})'


class CallParticipant(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='call_participations',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    muted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['call', 'user'],
                condition=Q(left_at__isnull=True),
                name='unique_active_call_participant',
            ),
        ]

    def __str__(self):
        return f'{self.user} in {self.call}'

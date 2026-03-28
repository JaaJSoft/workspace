from django.db import models

from workspace.common.uuids import uuid_v7_or_v4

from .models import Calendar


class ExternalCalendar(models.Model):
    """Tracks an external ICS feed linked to a Calendar."""

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    calendar = models.OneToOneField(
        Calendar,
        on_delete=models.CASCADE,
        related_name='external_source',
    )
    url = models.URLField(max_length=2048)

    # Sync state
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_etag = models.CharField(max_length=255, blank=True, default='')
    last_error = models.TextField(blank=True, default='')
    sync_interval = models.PositiveIntegerField(
        default=900,
        help_text='Sync interval in seconds (default: 15 minutes)',
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['is_active', 'last_synced_at'], name='ext_active_synced'),
        ]

    def __str__(self):
        return f'External: {self.calendar.name}'

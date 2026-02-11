from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class Calendar(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=30, default='primary')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendars',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['owner', 'name'], name='cal_owner_name'),
        ]

    def __str__(self):
        return self.name


class CalendarSubscription(models.Model):
    """A user subscribing to another user's calendar."""
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_subscriptions',
    )
    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        related_name='subscriptions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'calendar'],
                name='unique_calendar_subscription',
            ),
        ]

    def __str__(self):
        return f'{self.user} → {self.calendar}'


class Event(models.Model):
    class RecurrenceFrequency(models.TextChoices):
        DAILY = 'daily', 'Daily'
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'
        YEARLY = 'yearly', 'Yearly'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        related_name='events',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=255, blank=True, default='')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_events',
    )

    # Recurrence fields
    recurrence_frequency = models.CharField(
        max_length=7, choices=RecurrenceFrequency.choices,
        null=True, blank=True, default=None,
    )
    recurrence_interval = models.PositiveSmallIntegerField(default=1)
    recurrence_end = models.DateTimeField(null=True, blank=True, default=None)

    # Exception fields (for modified/cancelled occurrences)
    recurrence_parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, default=None,
        related_name='exceptions',
    )
    original_start = models.DateTimeField(null=True, blank=True, default=None)
    is_cancelled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start']
        indexes = [
            models.Index(fields=['start', 'end']),
            models.Index(fields=['owner', 'start']),
            models.Index(fields=['calendar', 'start']),
            models.Index(fields=['recurrence_parent', 'original_start']),
            models.Index(fields=['recurrence_frequency', 'start']),
        ]

    @property
    def is_recurring(self):
        return self.recurrence_frequency is not None

    @property
    def is_exception(self):
        return self.recurrence_parent_id is not None

    def __str__(self):
        return self.title


class EventMember(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='members',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_invitations',
    )
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'user'],
                name='unique_event_member',
            ),
        ]
        indexes = [
            models.Index(fields=['status'], name='evtmember_status'),
            models.Index(fields=['user', 'status'], name='evtmember_user_status'),
        ]

    def __str__(self):
        return f'{self.user} — {self.event} ({self.status})'

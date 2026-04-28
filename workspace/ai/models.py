from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Q
from django.utils import timezone

from workspace.common.uuids import uuid_v7_or_v4


class BotProfile(models.Model):
    """Configuration for an AI bot linked to a Django User."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='bot_profile',
    )
    system_prompt = models.TextField(blank=True)
    model = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    supports_tools = models.BooleanField(default=True)
    supports_vision = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_bots',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Access control
    is_public = models.BooleanField(default=False)
    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='allowed_bots',
    )
    allowed_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name='allowed_bots',
    )

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f'Bot: {self.user.get_full_name() or self.user.username}'

    def get_model(self):
        """Return the model to use, falling back to the global default."""
        return self.model or settings.AI_MODEL

    def is_accessible_by(self, user) -> bool:
        """Check if a user can access this bot."""
        if self.is_public or user.is_superuser:
            return True
        if self.created_by_id == user.id:
            return True
        if self.allowed_users.filter(pk=user.pk).exists():
            return True
        if self.allowed_groups.filter(user=user).exists():
            return True
        return False

    @classmethod
    def accessible_by(cls, user):
        """Return a queryset of BotProfiles accessible by the given user."""
        qs = cls.objects.filter(user__is_active=True)
        if user.is_superuser:
            return qs
        return qs.filter(
            Q(is_public=True)
            | Q(created_by=user)
            | Q(allowed_users=user)
            | Q(allowed_groups__user=user)
        ).distinct()


class AITask(models.Model):
    """Tracks an async AI operation (summarize, compose, etc.)."""
    class Status(models.TextChoices):
        PENDING = 'pending'
        PROCESSING = 'processing'
        COMPLETED = 'completed'
        FAILED = 'failed'

    class TaskType(models.TextChoices):
        SUMMARIZE = 'summarize'
        COMPOSE = 'compose'
        REPLY = 'reply'
        CHAT = 'chat'
        EDITOR = 'editor'
        CLASSIFY = 'classify'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_tasks',
    )
    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    input_data = models.JSONField(default=dict)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)

    model_used = models.CharField(max_length=100, blank=True)
    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    raw_messages = models.JSONField(null=True, blank=True)

    chat_message = models.ForeignKey(
        'chat.Message',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='ai_tasks',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'status', '-created_at'], name='aitask_owner_status'),
        ]

    def __str__(self):
        return f'AITask {self.uuid} ({self.task_type} - {self.status})'


class ConversationSummary(models.Model):
    """Rolling AI summary of older messages in a bot conversation."""
    conversation = models.OneToOneField(
        'chat.Conversation',
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='ai_summary_obj',
    )
    content = models.TextField(blank=True, default='')
    up_to = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Summary: {self.conversation_id}'


class UserMemory(models.Model):
    """Persistent memory that a bot stores about a user."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_memories',
    )
    bot = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bot_memories',
    )
    key = models.CharField(max_length=100)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'bot', 'key')
        ordering = ['key']

    def __str__(self):
        return f'Memory: {self.user.username}/{self.bot.username} — {self.key}'


class ScheduledMessage(models.Model):
    """Bot-initiated scheduled message (one-time or recurring)."""

    class Kind(models.TextChoices):
        ONCE = 'once', 'Once'
        RECURRING = 'recurring', 'Recurring'

    class RecurrenceUnit(models.TextChoices):
        HOURS = 'hours', 'Hours'
        DAYS = 'days', 'Days'
        WEEKS = 'weeks', 'Weeks'
        MONTHS = 'months', 'Months'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        'chat.Conversation',
        on_delete=models.CASCADE,
        related_name='scheduled_messages',
    )
    bot = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bot_scheduled_messages',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_scheduled_messages',
    )
    prompt = models.TextField()

    kind = models.CharField(max_length=10, choices=Kind.choices)
    scheduled_at = models.DateTimeField(null=True, blank=True)

    recurrence_unit = models.CharField(
        max_length=10,
        choices=RecurrenceUnit.choices,
        blank=True,
        default='',
    )
    recurrence_interval = models.PositiveIntegerField(default=1)
    recurrence_time = models.TimeField(null=True, blank=True)
    recurrence_day = models.PositiveIntegerField(null=True, blank=True)

    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['next_run_at']
        indexes = [
            # Partial index for the dispatch worker, which only ever queries
            # active schedules with `next_run_at <= now`. Skips inactive rows
            # entirely, keeping the index small even after many one-shot
            # schedules have completed.
            models.Index(
                fields=['next_run_at'],
                name='scheduled_active_next_run',
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self):
        return f'ScheduledMessage {self.uuid} ({self.kind} — {self.conversation_id})'

    def compute_next_run(self, user_tz=None):
        """Calculate and set the next run time, or deactivate for one-time messages.

        If *user_tz* is provided (a ``ZoneInfo``), ``recurrence_time`` is
        interpreted in that timezone and the result is converted back to UTC.
        Without it, ``recurrence_time`` is applied directly (legacy UTC
        behaviour).
        """
        from zoneinfo import ZoneInfo

        if self.kind == self.Kind.ONCE:
            self.is_active = False
            return

        utc = ZoneInfo('UTC')
        now = timezone.now()
        base = self.last_run_at or self.next_run_at or now

        has_local_time = self.recurrence_time is not None and user_tz is not None

        if self.recurrence_unit == self.RecurrenceUnit.HOURS:
            delta = timezone.timedelta(hours=self.recurrence_interval)
            self.next_run_at = base + delta

        elif self.recurrence_unit == self.RecurrenceUnit.DAYS:
            if has_local_time:
                base_local = base.astimezone(user_tz)
                candidate = base_local + timezone.timedelta(days=self.recurrence_interval)
                candidate = candidate.replace(
                    hour=self.recurrence_time.hour,
                    minute=self.recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
                self.next_run_at = candidate.astimezone(utc)
            else:
                delta = timezone.timedelta(days=self.recurrence_interval)
                candidate = base + delta
                if self.recurrence_time is not None:
                    candidate = candidate.replace(
                        hour=self.recurrence_time.hour,
                        minute=self.recurrence_time.minute,
                        second=self.recurrence_time.second,
                        microsecond=0,
                    )
                self.next_run_at = candidate

        elif self.recurrence_unit == self.RecurrenceUnit.WEEKS:
            if has_local_time:
                base_local = base.astimezone(user_tz)
                candidate = base_local + timezone.timedelta(weeks=self.recurrence_interval)
                if self.recurrence_day is not None:
                    current_weekday = candidate.weekday()
                    day_offset = (self.recurrence_day - current_weekday) % 7
                    candidate = candidate + timezone.timedelta(days=day_offset)
                candidate = candidate.replace(
                    hour=self.recurrence_time.hour,
                    minute=self.recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
                self.next_run_at = candidate.astimezone(utc)
            else:
                delta = timezone.timedelta(weeks=self.recurrence_interval)
                candidate = base + delta
                if self.recurrence_day is not None:
                    current_weekday = candidate.weekday()
                    day_offset = (self.recurrence_day - current_weekday) % 7
                    candidate = candidate + timezone.timedelta(days=day_offset)
                if self.recurrence_time is not None:
                    candidate = candidate.replace(
                        hour=self.recurrence_time.hour,
                        minute=self.recurrence_time.minute,
                        second=self.recurrence_time.second,
                        microsecond=0,
                    )
                self.next_run_at = candidate

        elif self.recurrence_unit == self.RecurrenceUnit.MONTHS:
            import calendar
            if has_local_time:
                base_local = base.astimezone(user_tz)
                year = base_local.year
                month = base_local.month + self.recurrence_interval
            else:
                year = base.year
                month = base.month + self.recurrence_interval
            year += (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = (base.astimezone(user_tz) if has_local_time else base).day
            if self.recurrence_day is not None:
                day = self.recurrence_day
            max_day = calendar.monthrange(year, month)[1]
            day = min(day, max_day)
            if has_local_time:
                candidate = base_local.replace(year=year, month=month, day=day)
                candidate = candidate.replace(
                    hour=self.recurrence_time.hour,
                    minute=self.recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
                self.next_run_at = candidate.astimezone(utc)
            else:
                candidate = base.replace(year=year, month=month, day=day)
                if self.recurrence_time is not None:
                    candidate = candidate.replace(
                        hour=self.recurrence_time.hour,
                        minute=self.recurrence_time.minute,
                        second=self.recurrence_time.second,
                        microsecond=0,
                    )
                self.next_run_at = candidate

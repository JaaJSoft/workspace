from django.conf import settings

from workspace.common.uuids import uuid_v7_or_v4
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Q


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
        if user.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(
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

    def __str__(self):
        return f'AITask {self.uuid} ({self.task_type} - {self.status})'


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

import uuid

from django.conf import settings
from django.db import models


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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_bots',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f'Bot: {self.user.get_full_name() or self.user.username}'

    def get_model(self):
        """Return the model to use, falling back to the global default."""
        return self.model or settings.AI_MODEL


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

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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

from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class Conversation(models.Model):
    class Kind(models.TextChoices):
        DM = 'dm', 'Direct Message'
        GROUP = 'group', 'Group'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    kind = models.CharField(max_length=5, choices=Kind.choices)
    title = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_conversations',
    )
    has_avatar = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['-updated_at']),
            models.Index(fields=['kind'], name='conv_kind'),
        ]

    def __str__(self):
        return self.title or f'{self.kind} — {self.uuid}'


class ConversationMember(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='members',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_memberships',
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['conversation', 'user'],
                name='unique_conversation_member',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'left_at']),
        ]

    def __str__(self):
        return f'{self.user} in {self.conversation}'


class Message(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_messages',
    )
    reply_to = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replies',
    )
    body = models.TextField()
    body_html = models.TextField(blank=True, default='')
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['conversation', '-created_at']),
            models.Index(fields=['deleted_at'], name='msg_deleted_at'),
        ]

    def __str__(self):
        return f'Message by {self.author} at {self.created_at}'


class Reaction(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='reactions',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    emoji = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['message', 'user', 'emoji'],
                name='unique_reaction',
            ),
        ]

    def __str__(self):
        return f'{self.user} reacted {self.emoji}'


class PinnedMessage(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='pinned_messages')
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='pinned_in')
    pinned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'message'], name='unique_pinned_message'),
        ]
        ordering = ['-created_at']
        indexes = [models.Index(fields=['conversation', '-created_at'])]

    def __str__(self):
        return f'Pin {self.message_id} in {self.conversation_id}'


class PinnedConversation(models.Model):
    """User-pinned conversations for quick sidebar access."""
    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pinned_conversations',
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='pins',
    )
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'conversation'],
                name='unique_pinned_conversation',
            ),
        ]
        ordering = ['position', 'created_at']
        indexes = [
            models.Index(fields=['owner', 'position']),
        ]

    def __str__(self):
        return f'{self.owner} pinned {self.conversation}'


def attachment_upload_path(instance, filename):
    return f"chat/{instance.message.conversation_id}/{instance.message_id}/{filename}"


class MessageAttachment(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=attachment_upload_path)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255, default='application/octet-stream')
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['message', 'created_at'], name='attach_msg_created'),
        ]

    @property
    def is_image(self):
        return self.mime_type.startswith('image/')

    def __str__(self):
        return f'{self.original_name} ({self.message_id})'

from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class Conversation(models.Model):
    class Kind(models.TextChoices):
        DM = "dm", "Direct Message"
        GROUP = "group", "Group"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    kind = models.CharField(max_length=5, choices=Kind.choices)
    title = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_conversations",
    )
    has_avatar = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["-updated_at"]),
        ]

    def __str__(self):
        return self.title or f"{self.kind} — {self.uuid}"


class ConversationMember(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_memberships",
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="unique_conversation_member",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "left_at"]),
        ]

    def __str__(self):
        return f"{self.user} in {self.conversation}"


class Message(models.Model):
    class Kind(models.TextChoices):
        USER = "user", "User"
        SYSTEM = "system", "System"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.USER)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    body = models.TextField()
    body_html = models.TextField(blank=True, default="")
    tool_data = models.JSONField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # B-tree is bidirectional in PostgreSQL and SQLite: this single index
            # serves both ASC and DESC ordering on (conversation, created_at).
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["deleted_at"], name="msg_deleted_at"),
        ]

    def __str__(self):
        return f"Message by {self.author} at {self.created_at}"


class Reaction(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="reactions",
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
                fields=["message", "user", "emoji"],
                name="unique_reaction",
            ),
        ]
        indexes = [
            models.Index(fields=["message", "emoji"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user} reacted {self.emoji}"


class PinnedMessage(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="pinned_messages"
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="pinned_in"
    )
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "message"], name="unique_pinned_message"
            ),
        ]
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["conversation", "-created_at"])]

    def __str__(self):
        return f"Pin {self.message_id} in {self.conversation_id}"


class PinnedConversation(models.Model):
    """User-pinned conversations for quick sidebar access."""

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pinned_conversations",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="pins",
    )
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "conversation"],
                name="unique_pinned_conversation",
            ),
        ]
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["owner", "position"]),
        ]

    def __str__(self):
        return f"{self.owner} pinned {self.conversation}"


def attachment_upload_path(instance, filename):
    import os

    ext = os.path.splitext(filename)[1]
    return f"chat/{instance.message.conversation_id}/{instance.uuid}{ext}"


class MessageAttachment(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=attachment_upload_path, max_length=500)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255, default="application/octet-stream")
    type = models.CharField(max_length=50, default="unknown", db_index=True)
    category = models.CharField(max_length=20, default="unknown", db_index=True)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["message", "created_at"], name="attach_msg_created"),
        ]

    def __str__(self):
        return f"{self.original_name} ({self.message_id})"

    @property
    def is_image(self):
        return self.category == "image" or (
            self.category == "unknown" and self.mime_type.startswith("image/")
        )

    @property
    def is_video(self):
        return self.category == "video" or (
            self.category == "unknown" and self.mime_type.startswith("video/")
        )


class LinkPreview(models.Model):
    """Cached OpenGraph metadata for a URL. Shared across messages."""

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    url = models.URLField(max_length=2048, unique=True)
    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    image_url = models.URLField(max_length=2048, blank=True, default="")
    favicon_url = models.URLField(max_length=500, blank=True, default="")
    site_name = models.CharField(max_length=200, blank=True, default="")
    fetched_at = models.DateTimeField(auto_now=True)
    fetch_failed = models.BooleanField(default=False)

    def __str__(self):
        return self.title or self.url[:80]


class MessageLinkPreview(models.Model):
    """Links a Message to its LinkPreview(s), preserving order."""

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="link_previews"
    )
    preview = models.ForeignKey(
        LinkPreview, on_delete=models.CASCADE, related_name="message_links"
    )
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "preview"], name="unique_msg_link_preview"
            ),
        ]
        ordering = ["position"]
        indexes = [
            models.Index(fields=["message", "position"], name="msglp_msg_pos"),
        ]

    def __str__(self):
        return f"Preview {self.preview_id} on {self.message_id}"


class MessageInteraction(models.Model):
    """Interactive content attached to a chat message (e.g. an AI question with
    clickable answer suggestions). Generic shape via ``kind`` + ``payload``
    / ``state`` so future kinds (poll, rating) reuse the same table.
    """

    class Kind(models.TextChoices):
        QUESTION = "question", "Question"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="interaction",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    payload = models.JSONField()
    state = models.JSONField(null=True, blank=True)
    interacted_at = models.DateTimeField(null=True, blank=True)
    interacted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="message_interactions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["interacted_at"]),
        ]

    def __str__(self):
        state = "pending" if self.interacted_at is None else "answered"
        return f"{self.kind} on {self.message_id} ({state})"


class CallSession(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    class MediaKind(models.TextChoices):
        AUDIO = "audio", "Audio"
        VIDEO = "video", "Video"
        SCREEN = "screen", "Screen"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="call_sessions"
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    state = models.CharField(max_length=8, choices=State.choices, default=State.ACTIVE)
    media_kind = models.CharField(
        max_length=8, choices=MediaKind.choices, default=MediaKind.AUDIO
    )
    system_message = models.OneToOneField(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_session",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(state="active"),
                name="one_active_call_per_conversation",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "state"]),
            models.Index(fields=["state"]),
        ]

    def __str__(self):
        return f"Call {self.uuid} in {self.conversation_id} ({self.state})"

    @property
    def duration_seconds(self):
        if self.ended_at is None:
            return None
        return int((self.ended_at - self.started_at).total_seconds())


class CallParticipant(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    session = models.ForeignKey(
        CallSession, on_delete=models.CASCADE, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user"], name="unique_call_participant"
            ),
        ]
        indexes = [
            models.Index(fields=["session", "left_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} in call {self.session_id}"

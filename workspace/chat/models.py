import secrets

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
    # Attached auth.Groups: membership follows their union (see services/group_sync).
    groups = models.ManyToManyField(
        "auth.Group",
        blank=True,
        related_name="conversations",
    )
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
    class NotificationLevel(models.TextChoices):
        """How much of a conversation reaches the bell and the push channel.

        Scopes notifications only: the unread badge and the live message
        delivery are untouched, so a silenced conversation still shows new
        messages when the user goes looking for them.
        """

        ALL = "all", "All messages"
        MENTIONS = "mentions", "Mentions only"
        NONE = "none", "Nothing"

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
    notification_level = models.CharField(
        max_length=8,
        choices=NotificationLevel.choices,
        default=NotificationLevel.ALL,
    )

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
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    # Pairing with conversation (guest.meeting.conversation must equal this
    # message's conversation) is a service-layer invariant, not a database one.
    # Removing a guest is a state transition (State.REMOVED + removed_at), never
    # a row delete: on_delete=CASCADE would take their messages with it.
    guest = models.ForeignKey(
        "MeetingGuest",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    # NULL on a thread's root message, set on every reply. The main conversation
    # flow is therefore `thread_root__isnull=True`. Replies to a reply are
    # flattened onto the same root: a thread is a list, never a tree.
    thread_root = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="thread_replies",
    )
    # Denormalised onto the root so a 50-message page does not aggregate per row.
    reply_count = models.PositiveIntegerField(default=0)
    last_reply_at = models.DateTimeField(null=True, blank=True)
    body = models.TextField()
    body_html = models.TextField(blank=True, default="")
    tool_data = models.JSONField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(author__isnull=False, guest__isnull=True)
                | models.Q(author__isnull=True, guest__isnull=False),
                name="message_one_identity",
            ),
        ]
        indexes = [
            # B-tree is bidirectional in PostgreSQL and SQLite: this single index
            # serves both ASC and DESC ordering on (conversation, created_at).
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["deleted_at"], name="msg_deleted_at"),
            models.Index(
                fields=["conversation", "thread_root", "created_at"],
                name="msg_conv_thread_created",
            ),
        ]

    def __str__(self):
        return f"Message by {self.author} at {self.created_at}"

    @property
    def media_attachments(self):
        # Iterates attachments.all() so a prefetch cache is reused as-is.
        # The is_audio exclusion is load-bearing: a recorded voice message is
        # category=video (the container really is a WebM) and pinned to the
        # audio viewer, so it satisfies both predicates.
        return [
            a
            for a in self.attachments.all()
            if (a.is_image or a.is_video) and not a.is_audio
        ]

    @property
    def audio_attachments(self):
        return [a for a in self.attachments.all() if a.is_audio]

    @property
    def file_attachments(self):
        return [
            a
            for a in self.attachments.all()
            if not (a.is_image or a.is_video or a.is_audio)
        ]


class ThreadParticipant(models.Model):
    """Who takes part in a thread, and how much of it they have read.

    A row appears when a user authors the root, posts a reply, or is mentioned
    in one. It is never toggled by hand: participation is derived. The table
    does two jobs, read state and notification recipients, so the two can never
    disagree about who cares about a thread.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    root_message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_thread_participations",
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["root_message", "user"],
                name="unique_thread_participant",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "root_message"]),
        ]

    def __str__(self):
        return f"{self.user} in thread {self.root_message_id}"


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
    viewer = models.CharField(max_length=32, blank=True, default="")
    duration_seconds = models.FloatField(null=True, blank=True)
    ai_description = models.TextField(blank=True, default="")
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

    @property
    def effective_viewer(self):
        """Pinned viewer slug, else the one derived from the content type."""
        if self.viewer:
            return self.viewer
        from workspace.files.services.filetype import get_viewer_slug

        return get_viewer_slug(self.type, self.original_name)

    @property
    def is_audio(self):
        return self.effective_viewer == "audio"


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
    # The live value while this session is active.
    # Meeting.locked_occurrence_start is the durable one - it seeds this
    # field at creation (see start_or_join_call) and is written alongside it
    # on every set_locked call, so a host can lock an empty room and still
    # find it locked once someone joins. This one dies with the session,
    # which ends as soon as its last participant leaves; the durable one
    # outlives that on purpose and keeps the room shut for the rest of the
    # occurrence it names.
    locked = models.BooleanField(default=False)

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
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    guest = models.ForeignKey(
        "MeetingGuest",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False, guest__isnull=True)
                | models.Q(user__isnull=True, guest__isnull=False),
                name="call_participant_one_identity",
            ),
            # A UniqueConstraint over a nullable column does not constrain NULL
            # rows, so the old single constraint becomes one per identity.
            models.UniqueConstraint(
                fields=["session", "user"],
                condition=models.Q(user__isnull=False),
                name="unique_call_participant_user",
            ),
            models.UniqueConstraint(
                fields=["session", "guest"],
                condition=models.Q(guest__isnull=False),
                name="unique_call_participant_guest",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "left_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} in call {self.session_id}"

    @property
    def participant_key(self):
        """Routing identity for signalling, presence and the peer table."""
        from workspace.chat.services.participant_keys import guest_key, user_key

        if self.user_id is not None:
            return user_key(self.user_id)
        return guest_key(self.guest_id)


def _generate_meeting_slug():
    return secrets.token_urlsafe(16)


class Meeting(models.Model):
    """A joinable meeting attached to a calendar event.

    One row per event, so the join URL is stable across a recurring series;
    which occurrence is currently open is derived per request rather than
    stored (see services/meeting_occurrences.py).
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    event = models.OneToOneField(
        "calendar.Event", on_delete=models.CASCADE, related_name="meeting"
    )
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="meeting"
    )
    slug = models.CharField(max_length=32, unique=True, default=_generate_meeting_slug)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    # The start of the occurrence the host most recently ended. A later
    # occurrence has a different start, so the same URL opens again next week.
    closed_occurrence_start = models.DateTimeField(null=True, blank=True)
    # Durable lock: the start of the occurrence it was set during (always
    # current_occurrence()'s output, never event.start), so the value carries
    # its own scope instead of being a bare boolean. It survives with no
    # CallSession at all, which is what lets a host pre-lock an empty room,
    # and it seeds CallSession.locked when a session is created.
    #
    # The invariant: a lock lives until the host unlocks, the host presses
    # End, or the occurrence it names stops being the current one. It
    # therefore survives a call that empties out and the stale-participant
    # sweep - neither is the host reopening the room. A non-null value naming
    # an elapsed occurrence is inert, not "locked": nothing purges it, and
    # nothing needs to, because every reader compares it against the
    # occurrence reachable right now (see calls.is_call_locked).
    locked_occurrence_start = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Meeting {self.slug} for event {self.event_id}"

    @property
    def join_path(self):
        return f"/meet/{self.slug}"


class MeetingGuest(models.Model):
    """Someone joining a meeting from the link, with no user row.

    Deliberately not a user: a guest is scoped to one meeting and one
    occurrence, and holds no workspace access of any kind.
    """

    class State(models.TextChoices):
        WAITING = "waiting", "Waiting"
        ADMITTED = "admitted", "Admitted"
        REFUSED = "refused", "Refused"
        REMOVED = "removed", "Removed"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    meeting = models.ForeignKey(
        Meeting, on_delete=models.CASCADE, related_name="guests"
    )
    display_name = models.CharField(max_length=80)
    state = models.CharField(max_length=8, choices=State.choices, default=State.WAITING)
    occurrence_start = models.DateTimeField()
    # sha256 hex of the bearer token; the token itself is never stored.
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    admitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["meeting", "state"]),
            models.Index(fields=["meeting", "occurrence_start"]),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.state}) in {self.meeting_id}"

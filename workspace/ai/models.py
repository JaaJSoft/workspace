import logging
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.uuids import uuid_v7_or_v4

logger = logging.getLogger(__name__)

# Cap on a reference recording. The backend's own 5 MiB ceiling applies to
# the base64 payload, which is a third larger than the file; this leaves room
# for that and is still generous - ten seconds of speech weighs under 500 KB.
VOICE_REF_MAX_BYTES = 3 * 1024 * 1024


def bot_voice_reference_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".wav"
    return f"ai/voices/{instance.user_id}/{uuid_v7_or_v4()}{ext}"


def validate_voice_reference_size(value):
    try:
        size = value.size
    except OSError:
        # The blob is gone; `voice_reference()` reports that at synthesis
        # time. Saving an unrelated field should not be what surfaces it.
        return
    if size > VOICE_REF_MAX_BYTES:
        raise ValidationError(
            f"The recording is {size} bytes; at most {VOICE_REF_MAX_BYTES} "
            "reaches the speech backend. A few seconds of speech is enough."
        )


class BotProfile(models.Model):
    """Configuration for an AI bot linked to a Django User."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="bot_profile",
    )
    system_prompt = models.TextField(blank=True)
    model = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    supports_tools = models.BooleanField(default=True)
    supports_vision = models.BooleanField(default=True)
    voice = models.TextField(blank=True)
    voice_ref = models.FileField(
        upload_to=bot_voice_reference_path,
        max_length=500,
        blank=True,
        validators=[validate_voice_reference_size],
    )
    voice_ref_text = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_bots",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Access control
    is_public = models.BooleanField(default=False)
    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="allowed_bots",
    )
    allowed_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="allowed_bots",
    )

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"Bot: {self.user.get_full_name() or self.user.username}"

    def get_model(self):
        """Return the model to use, falling back to the global default."""
        return self.model or settings.AI_MODEL

    def get_voice(self) -> str:
        """Description of how this bot sounds, for the speech backend."""
        return self.voice or settings.AI_TTS_VOICE

    def clean(self):
        super().clean()
        if self.voice_ref and not self.voice_ref_text.strip():
            raise ValidationError(
                {
                    "voice_ref_text": (
                        "Transcribe the recording word for word - the speech "
                        "model aligns the clone on it."
                    )
                }
            )
        if self.voice_ref_text.strip() and not self.voice_ref:
            raise ValidationError(
                {"voice_ref": "A transcript on its own clones no voice."}
            )

    def voice_reference(self):
        """The recording this bot speaks through, or None when it has none.

        None is also the answer to a half-filled pair or a vanished blob:
        the description is a usable second-best, while a clone missing
        either half is a hard error at the backend.
        """
        from .services.speech import VoiceReference

        transcript = self.voice_ref_text.strip()
        if not self.voice_ref or not transcript:
            return None
        try:
            with self.voice_ref.open("rb") as recording:
                audio = recording.read()
        except OSError as exc:
            logger.warning(
                "Voice reference of bot %s is unreadable at %s: %s",
                scrub(str(self.user_id)),
                scrub(self.voice_ref.name),
                scrub(str(exc)),
            )
            return None
        return VoiceReference(audio=audio, text=transcript) if audio else None

    def is_accessible_by(self, user) -> bool:
        """Check if a user can access this bot."""
        if self.is_public or user.is_superuser:
            return True
        if self.created_by_id == user.id:
            return True
        # Single round-trip combining the two M2M checks.
        return (
            BotProfile.objects.filter(pk=self.pk)
            .filter(Q(allowed_users=user) | Q(allowed_groups__user=user))
            .exists()
        )

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
        PENDING = "pending"
        PROCESSING = "processing"
        COMPLETED = "completed"
        FAILED = "failed"

    class TaskType(models.TextChoices):
        SUMMARIZE = "summarize"
        COMPOSE = "compose"
        REPLY = "reply"
        CHAT = "chat"
        EDITOR = "editor"
        CLASSIFY = "classify"
        EXTRACT = "extract"
        AGENT = "agent"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_tasks",
    )
    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    input_data = models.JSONField(default=dict)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)

    model_used = models.CharField(max_length=100, blank=True)
    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    raw_messages = models.JSONField(null=True, blank=True)

    chat_message = models.ForeignKey(
        "chat.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_tasks",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["owner", "status", "-created_at"], name="aitask_owner_status"
            ),
            # Serves the daily purge, which filters terminal tasks by
            # completed_at with no owner. The owner-led index above cannot
            # help that predicate, so without this the purge full-scans.
            models.Index(
                fields=["status", "completed_at"], name="aitask_status_completed"
            ),
        ]

    def __str__(self):
        return f"AITask {self.uuid} ({self.task_type} - {self.status})"


class ConversationSummary(models.Model):
    """Rolling AI summary of older messages in a bot conversation."""

    conversation = models.OneToOneField(
        "chat.Conversation",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="ai_summary_obj",
    )
    content = models.TextField(blank=True, default="")
    up_to = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Summary: {self.conversation_id}"


class UserMemory(models.Model):
    """Persistent memory that a bot stores about a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_memories",
    )
    bot = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bot_memories",
    )
    key = models.CharField(max_length=100)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "bot", "key")
        ordering = ["key"]

    def __str__(self):
        return f"Memory: {self.user.username}/{self.bot.username} — {self.key}"


class AgentGoal(models.Model):
    """Long-horizon autonomous goal a bot pursues across days or months.

    Unlike :class:`ScheduledMessage` (fires at a fixed time or recurrence),
    the bot decides at each check-in when to wake up next, keeps private
    working notes as its memory between check-ins, and only messages the
    user when it judges it has something worth saying.
    """

    class Status(models.TextChoices):
        ACTIVE = "active"
        PAUSED = "paused"
        COMPLETED = "completed"
        ABANDONED = "abandoned"

    # Floor between two autonomous check-ins of the same goal: every wake-up
    # is a full LLM tool-loop run, so an agent must not be able to schedule
    # itself into a tight loop.
    MIN_CHECK_INTERVAL = timedelta(minutes=5)
    # Applied by the worker before the run; the agent overrides it by setting
    # its own next check-in. A crashed or forgetful run resumes in a day
    # instead of going quiet forever (or re-firing immediately).
    FALLBACK_CHECK_INTERVAL = timedelta(hours=24)
    MAX_ACTIVE_PER_CONVERSATION = 20
    # How long a closed goal keeps surfacing in list_agent_goals. A goal absent
    # from that listing is indistinguishable from one that never existed, which
    # is what makes an agent re-open it or speak about it as still running.
    CLOSED_RECALL_WINDOW = timedelta(days=30)
    CLOSED_RECALL_LIMIT = 10

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        "chat.Conversation",
        on_delete=models.CASCADE,
        related_name="agent_goals",
    )
    bot = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bot_agent_goals",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_agent_goals",
    )

    title = models.CharField(max_length=200)
    goal = models.TextField()
    # Mission brief: user-owned steering text injected in every check-in
    # prompt. Each field maps to one decision the agent makes on its own —
    # when to close the goal, how to work on it, and when to break silence.
    success_criteria = models.TextField(blank=True, default="")
    constraints = models.TextField(blank=True, default="")
    reporting = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    outcome = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    deadline = models.DateTimeField(null=True, blank=True)

    next_check_at = models.DateTimeField()
    last_checked_at = models.DateTimeField(null=True, blank=True)
    # Set when the goal leaves active/paused. `updated_at` cannot stand in for
    # it: it moves on any later write, while the recall window needs a date
    # that is fixed at closing time.
    closed_at = models.DateTimeField(null=True, blank=True)
    check_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_check_at"]
        indexes = [
            # Partial index for the dispatch worker, which only ever queries
            # active goals with `next_check_at <= now` — mirrors
            # scheduled_active_next_run on ScheduledMessage.
            models.Index(
                fields=["next_check_at"],
                name="agentgoal_active_next_check",
                condition=models.Q(status="active"),
            ),
        ]

    def __str__(self):
        return f"AgentGoal {self.uuid} ({self.status} — {self.title[:40]})"

    @classmethod
    def clamp_next_check(cls, dt):
        """Enforce the minimum spacing between autonomous check-ins."""
        floor = timezone.now() + cls.MIN_CHECK_INTERVAL
        return max(dt, floor)


class ScheduledMessage(models.Model):
    """Bot-initiated scheduled message (one-time or recurring)."""

    class Kind(models.TextChoices):
        ONCE = "once", "Once"
        RECURRING = "recurring", "Recurring"

    class RecurrenceUnit(models.TextChoices):
        HOURS = "hours", "Hours"
        DAYS = "days", "Days"
        WEEKS = "weeks", "Weeks"
        MONTHS = "months", "Months"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        "chat.Conversation",
        on_delete=models.CASCADE,
        related_name="scheduled_messages",
    )
    bot = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bot_scheduled_messages",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_scheduled_messages",
    )
    prompt = models.TextField()

    kind = models.CharField(max_length=10, choices=Kind.choices)
    scheduled_at = models.DateTimeField(null=True, blank=True)

    recurrence_unit = models.CharField(
        max_length=10,
        choices=RecurrenceUnit.choices,
        blank=True,
        default="",
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
        ordering = ["next_run_at"]
        indexes = [
            # Partial index for the dispatch worker, which only ever queries
            # active schedules with `next_run_at <= now`. Skips inactive rows
            # entirely, keeping the index small even after many one-shot
            # schedules have completed.
            models.Index(
                fields=["next_run_at"],
                name="scheduled_active_next_run",
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self):
        return f"ScheduledMessage {self.uuid} ({self.kind} — {self.conversation_id})"

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

        utc = ZoneInfo("UTC")
        now = timezone.now()
        base = self.last_run_at or self.next_run_at or now

        has_local_time = self.recurrence_time is not None and user_tz is not None

        if self.recurrence_unit == self.RecurrenceUnit.HOURS:
            delta = timezone.timedelta(hours=self.recurrence_interval)
            self.next_run_at = base + delta

        elif self.recurrence_unit == self.RecurrenceUnit.DAYS:
            if has_local_time:
                base_local = base.astimezone(user_tz)
                candidate = base_local + timezone.timedelta(
                    days=self.recurrence_interval
                )
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
                candidate = base_local + timezone.timedelta(
                    weeks=self.recurrence_interval
                )
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

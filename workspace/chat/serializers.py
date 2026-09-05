from rest_framework import serializers

from workspace.ai.models import AgentGoal, ScheduledMessage

from .models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    MessageInteraction,
    PinnedMessage,
    Reaction,
)
from .services.avatar import conversation_avatar_initial
from .services.identities import identity_payload


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class ConversationMemberSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer()

    class Meta:
        model = ConversationMember
        fields = ["uuid", "user", "last_read_at", "joined_at", "left_at"]


class ReactionSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer()

    class Meta:
        model = Reaction
        fields = ["uuid", "emoji", "user", "created_at"]


class PinnedMessageSerializer(serializers.ModelSerializer):
    message_uuid = serializers.UUIDField(source="message.uuid")
    message_body = serializers.SerializerMethodField()
    message_author = serializers.SerializerMethodField()
    message_created_at = serializers.DateTimeField(source="message.created_at")
    pinned_by = MemberUserSerializer()
    pinned_at = serializers.DateTimeField(source="created_at")

    class Meta:
        model = PinnedMessage
        fields = [
            "uuid",
            "message_uuid",
            "message_body",
            "message_author",
            "message_created_at",
            "pinned_by",
            "pinned_at",
        ]

    def get_message_body(self, obj):
        body = obj.message.body or ""
        return body[:100] + "\u2026" if len(body) > 100 else body

    def get_message_author(self, obj):
        return identity_payload(obj.message.author, obj.message.guest)


class MessageAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    is_image = serializers.BooleanField(read_only=True)
    is_video = serializers.BooleanField(read_only=True)
    is_audio = serializers.BooleanField(read_only=True)

    class Meta:
        model = MessageAttachment
        fields = [
            "uuid",
            "original_name",
            "mime_type",
            "type",
            "size",
            "is_image",
            "is_video",
            "is_audio",
            "duration_seconds",
            "url",
            "created_at",
        ]

    def get_url(self, obj):
        return f"/api/v1/chat/attachments/{obj.uuid}"


class ReplyToSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["uuid", "author", "body", "deleted_at"]
        read_only_fields = fields

    def get_body(self, obj):
        body = obj.body or ""
        return body[:200] + "\u2026" if len(body) > 200 else body

    def get_author(self, obj):
        return identity_payload(obj.author, obj.guest)


class LinkPreviewSerializer(serializers.Serializer):
    url = serializers.URLField(source="preview.url")
    title = serializers.CharField(source="preview.title")
    description = serializers.CharField(source="preview.description")
    image_url = serializers.URLField(source="preview.image_url", allow_blank=True)
    favicon_url = serializers.URLField(source="preview.favicon_url", allow_blank=True)
    site_name = serializers.CharField(source="preview.site_name")


class MessageInteractionSerializer(serializers.ModelSerializer):
    interacted_by = MemberUserSerializer(read_only=True)

    class Meta:
        model = MessageInteraction
        fields = [
            "uuid",
            "kind",
            "payload",
            "state",
            "interacted_at",
            "interacted_by",
        ]


class MessageSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    reactions = ReactionSerializer(many=True, read_only=True)
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    link_previews = LinkPreviewSerializer(many=True, read_only=True)
    conversation_id = serializers.UUIDField()
    reply_to = ReplyToSerializer(read_only=True, allow_null=True)
    thread_root = serializers.UUIDField(
        source="thread_root_id", read_only=True, allow_null=True
    )
    interaction = MessageInteractionSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Message
        fields = [
            "uuid",
            "kind",
            "tool_data",
            "conversation_id",
            "author",
            "body",
            "body_html",
            "edited_at",
            "created_at",
            "deleted_at",
            "reactions",
            "attachments",
            "link_previews",
            "reply_to",
            "thread_root",
            "reply_count",
            "last_reply_at",
            "interaction",
        ]

    def get_author(self, obj):
        return identity_payload(obj.author, obj.guest)


class GuestMessageSerializer(MessageSerializer):
    """MessageSerializer, redacted for a guest audience.

    Two things the plain serializer emits are unsafe once a guest is reading
    it: ``conversation_id`` (the same "must not learn to address the
    host-side conversation endpoints" invariant guest call state enforces),
    and a ``reply_to``/``thread_root`` pointing below this guest's occurrence
    floor. The top-level queryset floors at ``created_at >= occurrence_start``,
    but an in-window reply can legitimately target a pre-window message -
    ordinary behaviour in a recurring meeting's conversation - and
    reply_to/thread_root are hydrated from that target regardless of its own
    created_at. Left alone, ReplyToSerializer would hand a guest the
    pre-window body and author, and the bare thread_root UUID would let them
    name that pre-window message as reply_to_uuid on a POST - a pull
    primitive around the floor. Redacting post-serialization (rather than
    re-querying) is cheap: reply_to and thread_root are already
    select_related, so their created_at is already in memory.

    A subclass instead of touching MessageSerializer itself: the redaction is
    a guest-only concern with no meaning on the member path, and every other
    behaviour must resolve through get_author, ReplyToSerializer etc.
    unmodified.

    Public and imported from both the guest REST views and the guest SSE
    stream - it must stay the one place this redaction is implemented, or the
    two paths can silently drift apart on what a guest is allowed to read.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop("conversation_id", None)
        # A missing floor must fail loud (KeyError), never default to None -
        # a None floor would compare False to every created_at below it and
        # silently hand a guest pre-window reply_to/thread_root content.
        floor = self.context["floor"]
        if instance.reply_to_id and instance.reply_to.created_at < floor:
            data["reply_to"] = None
        if instance.thread_root_id and instance.thread_root.created_at < floor:
            data["thread_root"] = None
        return data


class LastMessageSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    has_attachments = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["uuid", "author", "body", "created_at", "has_attachments"]

    def get_has_attachments(self, obj):
        if (
            hasattr(obj, "_prefetched_objects_cache")
            and "attachments" in obj._prefetched_objects_cache
        ):
            return len(obj._prefetched_objects_cache["attachments"]) > 0
        return obj.attachments.exists()

    def get_author(self, obj):
        return identity_payload(obj.author, obj.guest)


class GroupBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class ConversationListSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)
    groups = GroupBriefSerializer(many=True, read_only=True)
    member_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True, default=0)
    is_pinned = serializers.BooleanField(read_only=True, default=False)
    pin_position = serializers.IntegerField(read_only=True, default=None)
    is_bot_conversation = serializers.SerializerMethodField()
    avatar_initial = serializers.SerializerMethodField()
    notification_level = serializers.CharField(
        read_only=True,
        default=ConversationMember.NotificationLevel.ALL,
    )

    class Meta:
        model = Conversation
        fields = [
            "uuid",
            "kind",
            "title",
            "description",
            "created_by_id",
            "created_at",
            "updated_at",
            "has_avatar",
            "avatar_initial",
            "members",
            "groups",
            "member_count",
            "last_message",
            "unread_count",
            "is_pinned",
            "pin_position",
            "is_bot_conversation",
            "notification_level",
        ]

    def get_member_count(self, obj):
        # Members are prefetched (filtered to active) by the view, so len()
        # of the cache is free. The fallback hits the DB only when an ad-hoc
        # caller serializes a conversation without priming the prefetch.
        cache = getattr(obj, "_prefetched_objects_cache", None)
        if cache and "members" in cache:
            return len(cache["members"])
        return obj.members.filter(left_at__isnull=True).count()

    def get_avatar_initial(self, obj) -> str:
        return conversation_avatar_initial(obj, self.context["request"].user)

    def get_is_bot_conversation(self, obj):
        """Check if this conversation includes a bot member."""
        if (
            hasattr(obj, "_prefetched_objects_cache")
            and "members" in obj._prefetched_objects_cache
        ):
            for member in obj.members.all():
                if hasattr(member.user, "bot_profile"):
                    return True
            return False
        return obj.members.filter(user__bot_profile__isnull=False).exists()

    def get_last_message(self, obj):
        # _last_message is set by the view; use sentinel to avoid fallback query
        if hasattr(obj, "_last_message"):
            msg = obj._last_message
        else:
            msg = (
                obj.messages.filter(deleted_at__isnull=True)
                .order_by("-created_at")
                .select_related("author", "guest")
                .first()
            )
        if msg:
            return LastMessageSerializer(msg, context=self.context).data
        return None


class ConversationDetailSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)
    groups = GroupBriefSerializer(many=True, read_only=True)
    avatar_initial = serializers.SerializerMethodField()
    notification_level = serializers.CharField(
        read_only=True,
        default=ConversationMember.NotificationLevel.ALL,
    )

    class Meta:
        model = Conversation
        fields = [
            "uuid",
            "kind",
            "title",
            "description",
            "created_by_id",
            "created_at",
            "updated_at",
            "has_avatar",
            "avatar_initial",
            "members",
            "groups",
            "notification_level",
        ]

    def get_avatar_initial(self, obj) -> str:
        return conversation_avatar_initial(obj, self.context["request"].user)


class NotificationLevelSerializer(serializers.Serializer):
    level = serializers.ChoiceField(
        choices=ConversationMember.NotificationLevel.choices
    )


class ConversationCreateSerializer(serializers.Serializer):
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        required=False,
    )
    group_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        required=False,
    )
    title = serializers.CharField(max_length=255, required=False, default="")
    description = serializers.CharField(required=False, default="", allow_blank=True)

    def validate(self, attrs):
        if bool(attrs.get("member_ids")) == bool(attrs.get("group_ids")):
            raise serializers.ValidationError(
                "Provide exactly one of member_ids or group_ids."
            )
        return attrs


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(required=False, default="", allow_blank=True)
    reply_to_uuid = serializers.UUIDField(required=False, allow_null=True)
    file_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    duration = serializers.FloatField(required=False, allow_null=True)


class MessageEditSerializer(serializers.Serializer):
    body = serializers.CharField()


class ReactionToggleSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=32)


class AgentGoalSerializer(serializers.ModelSerializer):
    bot_username = serializers.CharField(source="bot.username", read_only=True)
    bot_display_name = serializers.SerializerMethodField()

    class Meta:
        model = AgentGoal
        fields = [
            "uuid",
            "title",
            "goal",
            "success_criteria",
            "constraints",
            "reporting",
            "notes",
            "outcome",
            "status",
            "deadline",
            "next_check_at",
            "last_checked_at",
            "check_count",
            "bot_username",
            "bot_display_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "outcome",
            "last_checked_at",
            "check_count",
            "bot_username",
            "bot_display_name",
            "created_at",
            "updated_at",
        ]

    def get_bot_display_name(self, obj):
        return obj.bot.get_full_name() or obj.bot.username

    def validate_status(self, value):
        # Users can pause/resume a goal from the UI; closing one goes through
        # the DELETE endpoint (abandoned) or the bot's complete_agent_goal.
        if value not in (AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED):
            raise serializers.ValidationError(
                "Status can only be set to 'active' or 'paused'."
            )
        return value

    def validate_next_check_at(self, value):
        # Same floor the agent's own update_agent_goal tool obeys: rescheduling
        # a check-in from the UI must not fire a full LLM run right away.
        return AgentGoal.clamp_next_check(value)


class AgentGoalCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    goal = serializers.CharField()
    success_criteria = serializers.CharField(required=False, allow_blank=True)
    constraints = serializers.CharField(required=False, allow_blank=True)
    reporting = serializers.CharField(required=False, allow_blank=True)
    first_check_at = serializers.DateTimeField(required=False, allow_null=True)
    deadline = serializers.DateTimeField(required=False, allow_null=True)


class ScheduledMessageSerializer(serializers.ModelSerializer):
    bot_username = serializers.CharField(source="bot.username", read_only=True)
    bot_display_name = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledMessage
        fields = [
            "uuid",
            "prompt",
            "kind",
            "scheduled_at",
            "recurrence_unit",
            "recurrence_interval",
            "recurrence_time",
            "recurrence_day",
            "next_run_at",
            "last_run_at",
            "is_active",
            "bot_username",
            "bot_display_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "next_run_at",
            "last_run_at",
            "is_active",
            "bot_username",
            "bot_display_name",
            "created_at",
            "updated_at",
        ]

    def get_bot_display_name(self, obj):
        return obj.bot.get_full_name() or obj.bot.username

    def validate(self, attrs):
        # ONCE schedules derive next_run_at from scheduled_at, which is non-null
        # in the model. Resolve the effective (kind, scheduled_at) pair against
        # the existing instance for partial updates so we can't half-mutate a
        # recurring row into ONCE without supplying a fire time.
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if kind == ScheduledMessage.Kind.ONCE:
            if "scheduled_at" in attrs:
                scheduled_at = attrs["scheduled_at"]
            else:
                scheduled_at = getattr(self.instance, "scheduled_at", None)
            if scheduled_at is None:
                raise serializers.ValidationError(
                    {
                        "scheduled_at": 'scheduled_at is required when kind is "once".',
                    }
                )
        return attrs

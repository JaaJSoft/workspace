from rest_framework import serializers

from workspace.ai.models import ScheduledMessage

from .models import Conversation, ConversationMember, LinkPreview, Message, MessageAttachment, MessageLinkPreview, PinnedMessage, Reaction


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class ConversationMemberSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer()

    class Meta:
        model = ConversationMember
        fields = ['uuid', 'user', 'last_read_at', 'joined_at', 'left_at']


class ReactionSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer()

    class Meta:
        model = Reaction
        fields = ['uuid', 'emoji', 'user', 'created_at']


class PinnedMessageSerializer(serializers.ModelSerializer):
    message_uuid = serializers.UUIDField(source='message.uuid')
    message_body = serializers.SerializerMethodField()
    message_author = MemberUserSerializer(source='message.author')
    message_created_at = serializers.DateTimeField(source='message.created_at')
    pinned_by = MemberUserSerializer()
    pinned_at = serializers.DateTimeField(source='created_at')

    class Meta:
        model = PinnedMessage
        fields = ['uuid', 'message_uuid', 'message_body', 'message_author', 'message_created_at', 'pinned_by', 'pinned_at']

    def get_message_body(self, obj):
        body = obj.message.body or ''
        return body[:100] + '\u2026' if len(body) > 100 else body


class MessageAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    is_image = serializers.BooleanField(read_only=True)
    is_video = serializers.BooleanField(read_only=True)

    class Meta:
        model = MessageAttachment
        fields = ['uuid', 'original_name', 'mime_type', 'size', 'is_image', 'is_video', 'url', 'created_at']

    def get_url(self, obj):
        return f'/api/v1/chat/attachments/{obj.uuid}'


class ReplyToSerializer(serializers.ModelSerializer):
    author = MemberUserSerializer()
    body = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['uuid', 'author', 'body', 'deleted_at']
        read_only_fields = fields

    def get_body(self, obj):
        body = obj.body or ''
        return body[:200] + '\u2026' if len(body) > 200 else body


class LinkPreviewSerializer(serializers.Serializer):
    url = serializers.URLField(source='preview.url')
    title = serializers.CharField(source='preview.title')
    description = serializers.CharField(source='preview.description')
    image_url = serializers.URLField(source='preview.image_url', allow_blank=True)
    favicon_url = serializers.URLField(source='preview.favicon_url', allow_blank=True)
    site_name = serializers.CharField(source='preview.site_name')


class MessageSerializer(serializers.ModelSerializer):
    author = MemberUserSerializer()
    reactions = ReactionSerializer(many=True, read_only=True)
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    link_previews = LinkPreviewSerializer(many=True, read_only=True)
    conversation_id = serializers.UUIDField()
    reply_to = ReplyToSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Message
        fields = [
            'uuid', 'conversation_id', 'author', 'body', 'body_html',
            'edited_at', 'created_at', 'deleted_at',
            'reactions', 'attachments', 'link_previews', 'reply_to',
        ]


class LastMessageSerializer(serializers.ModelSerializer):
    author = MemberUserSerializer()
    has_attachments = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['uuid', 'author', 'body', 'created_at', 'has_attachments']

    def get_has_attachments(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'attachments' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['attachments']) > 0
        return obj.attachments.exists()


class ConversationListSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)
    member_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True, default=0)
    is_pinned = serializers.BooleanField(read_only=True, default=False)
    pin_position = serializers.IntegerField(read_only=True, default=None)
    is_bot_conversation = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'uuid', 'kind', 'title', 'description', 'created_by_id',
            'created_at', 'updated_at', 'has_avatar',
            'members', 'member_count', 'last_message', 'unread_count',
            'is_pinned', 'pin_position', 'is_bot_conversation',
        ]

    def get_member_count(self, obj):
        # Members are prefetched (filtered to active) by the view, so len()
        # of the cache is free. The fallback hits the DB only when an ad-hoc
        # caller serializes a conversation without priming the prefetch.
        cache = getattr(obj, '_prefetched_objects_cache', None)
        if cache and 'members' in cache:
            return len(cache['members'])
        return obj.members.filter(left_at__isnull=True).count()

    def get_is_bot_conversation(self, obj):
        """Check if this conversation includes a bot member."""
        if hasattr(obj, '_prefetched_objects_cache') and 'members' in obj._prefetched_objects_cache:
            for member in obj.members.all():
                if hasattr(member.user, 'bot_profile'):
                    return True
            return False
        return obj.members.filter(user__bot_profile__isnull=False).exists()

    def get_last_message(self, obj):
        # _last_message is set by the view; use sentinel to avoid fallback query
        if hasattr(obj, '_last_message'):
            msg = obj._last_message
        else:
            msg = (
                obj.messages
                .filter(deleted_at__isnull=True)
                .order_by('-created_at')
                .select_related('author')
                .first()
            )
        if msg:
            return LastMessageSerializer(msg, context=self.context).data
        return None


class ConversationDetailSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = [
            'uuid', 'kind', 'title', 'description', 'created_by_id',
            'created_at', 'updated_at', 'has_avatar', 'members',
        ]


class ConversationCreateSerializer(serializers.Serializer):
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
    )
    title = serializers.CharField(max_length=255, required=False, default='')
    description = serializers.CharField(required=False, default='', allow_blank=True)


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(required=False, default='', allow_blank=True)
    reply_to_uuid = serializers.UUIDField(required=False, allow_null=True)


class MessageEditSerializer(serializers.Serializer):
    body = serializers.CharField()


class ReactionToggleSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=32)


class ScheduledMessageSerializer(serializers.ModelSerializer):
    bot_username = serializers.CharField(source='bot.username', read_only=True)
    bot_display_name = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledMessage
        fields = [
            'uuid', 'prompt', 'kind',
            'scheduled_at', 'recurrence_unit', 'recurrence_interval',
            'recurrence_time', 'recurrence_day',
            'next_run_at', 'last_run_at', 'is_active',
            'bot_username', 'bot_display_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'uuid', 'next_run_at', 'last_run_at', 'is_active',
            'bot_username', 'bot_display_name',
            'created_at', 'updated_at',
        ]

    def get_bot_display_name(self, obj):
        return obj.bot.get_full_name() or obj.bot.username

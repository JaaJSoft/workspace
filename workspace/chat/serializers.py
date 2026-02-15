from rest_framework import serializers

from workspace.users.avatar_service import has_avatar

from .models import Conversation, ConversationMember, Message, MessageAttachment, PinnedMessage, Reaction


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, user):
        if has_avatar(user):
            return f'/api/v1/users/{user.id}/avatar'
        return None


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

    class Meta:
        model = MessageAttachment
        fields = ['uuid', 'original_name', 'mime_type', 'size', 'is_image', 'url', 'created_at']

    def get_url(self, obj):
        return f'/api/v1/chat/attachments/{obj.uuid}'


class MessageSerializer(serializers.ModelSerializer):
    author = MemberUserSerializer()
    reactions = ReactionSerializer(many=True, read_only=True)
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    conversation_id = serializers.UUIDField()

    class Meta:
        model = Message
        fields = [
            'uuid', 'conversation_id', 'author', 'body', 'body_html',
            'edited_at', 'created_at', 'deleted_at',
            'reactions', 'attachments',
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
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True, default=0)
    avatar_url = serializers.SerializerMethodField()
    is_pinned = serializers.BooleanField(read_only=True, default=False)
    pin_position = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = Conversation
        fields = [
            'uuid', 'kind', 'title', 'description', 'created_by_id',
            'created_at', 'updated_at',
            'members', 'last_message', 'unread_count',
            'avatar_url', 'is_pinned', 'pin_position',
        ]

    def get_last_message(self, obj):
        msg = getattr(obj, '_last_message', None)
        if msg is None:
            msg = (
                obj.messages
                .filter(deleted_at__isnull=True)
                .order_by('-created_at')
                .select_related('author')
                .first()
            )
        if msg:
            return LastMessageSerializer(msg).data
        return None

    def get_avatar_url(self, obj):
        if obj.has_avatar:
            return f'/api/v1/chat/conversations/{obj.uuid}/avatar/image'
        return None


class ConversationDetailSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'uuid', 'kind', 'title', 'description', 'created_by_id',
            'created_at', 'updated_at', 'members',
            'avatar_url',
        ]

    def get_avatar_url(self, obj):
        if obj.has_avatar:
            return f'/api/v1/chat/conversations/{obj.uuid}/avatar/image'
        return None


class ConversationCreateSerializer(serializers.Serializer):
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
    )
    title = serializers.CharField(max_length=255, required=False, default='')
    description = serializers.CharField(required=False, default='', allow_blank=True)


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(required=False, default='', allow_blank=True)


class MessageEditSerializer(serializers.Serializer):
    body = serializers.CharField()


class ReactionToggleSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=32)

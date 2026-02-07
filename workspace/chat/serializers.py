from rest_framework import serializers

from .models import Conversation, ConversationMember, Message, Reaction


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


class MessageSerializer(serializers.ModelSerializer):
    author = MemberUserSerializer()
    reactions = ReactionSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            'uuid', 'conversation_id', 'author', 'body', 'body_html',
            'edited_at', 'created_at', 'deleted_at',
            'reactions',
        ]


class LastMessageSerializer(serializers.ModelSerializer):
    author = MemberUserSerializer()

    class Meta:
        model = Message
        fields = ['uuid', 'author', 'body', 'created_at']


class ConversationListSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Conversation
        fields = [
            'uuid', 'kind', 'title', 'created_by_id',
            'created_at', 'updated_at',
            'members', 'last_message', 'unread_count',
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


class ConversationDetailSerializer(serializers.ModelSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = [
            'uuid', 'kind', 'title', 'created_by_id',
            'created_at', 'updated_at', 'members',
        ]


class ConversationCreateSerializer(serializers.Serializer):
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
    )
    title = serializers.CharField(max_length=255, required=False, default='')


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField()


class MessageEditSerializer(serializers.Serializer):
    body = serializers.CharField()


class ReactionToggleSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=32)

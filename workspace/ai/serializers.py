from rest_framework import serializers

from .models import AITask, BotProfile


class BotProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    user_id = serializers.IntegerField(source='user.id', read_only=True)

    class Meta:
        model = BotProfile
        fields = [
            'user_id', 'username', 'display_name', 'description',
            'avatar_url', 'created_at',
        ]

    def get_display_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_avatar_url(self, obj):
        return None


class AITaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = AITask
        fields = [
            'uuid', 'task_type', 'status', 'result', 'error',
            'model_used', 'prompt_tokens', 'completion_tokens',
            'created_at', 'completed_at',
        ]
        read_only_fields = fields


class SummarizeRequestSerializer(serializers.Serializer):
    message_id = serializers.UUIDField(help_text='UUID of the mail message to summarize.')


class ComposeRequestSerializer(serializers.Serializer):
    instructions = serializers.CharField(help_text='What the email should say.')
    context = serializers.CharField(required=False, default='', help_text='Optional context.')
    account_id = serializers.UUIDField(required=False, help_text='Mail account for tone matching.')


class ReplyRequestSerializer(serializers.Serializer):
    message_id = serializers.UUIDField(help_text='UUID of the mail message to reply to.')
    instructions = serializers.CharField(help_text='Instructions for the reply.')

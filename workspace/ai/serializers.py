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
    result_html = serializers.SerializerMethodField()

    class Meta:
        model = AITask
        fields = [
            'uuid', 'task_type', 'status', 'result', 'result_html', 'error',
            'model_used', 'prompt_tokens', 'completion_tokens',
            'created_at', 'completed_at',
        ]
        read_only_fields = fields

    def get_result_html(self, obj):
        if (
            obj.task_type == AITask.TaskType.EDITOR
            and obj.status == AITask.Status.COMPLETED
            and obj.input_data.get('action') in ('explain', 'summarize')
            and obj.result
        ):
            import mistune
            return mistune.html(obj.result)
        return None


class SummarizeRequestSerializer(serializers.Serializer):
    message_id = serializers.UUIDField(help_text='UUID of the mail message to summarize.')


class ComposeRequestSerializer(serializers.Serializer):
    instructions = serializers.CharField(help_text='What the email should say.')
    context = serializers.CharField(required=False, default='', help_text='Optional context.')
    account_id = serializers.UUIDField(required=False, help_text='Mail account for tone matching.')


class ReplyRequestSerializer(serializers.Serializer):
    message_id = serializers.UUIDField(help_text='UUID of the mail message to reply to.')
    instructions = serializers.CharField(help_text='Instructions for the reply.')


class EditorActionRequestSerializer(serializers.Serializer):
    ACTION_CHOICES = [
        ('improve', 'Improve'),
        ('explain', 'Explain'),
        ('summarize', 'Summarize'),
        ('custom', 'Custom'),
    ]

    action = serializers.ChoiceField(choices=ACTION_CHOICES, help_text='AI action to perform.')
    content = serializers.CharField(help_text='Text/code content to process.')
    language = serializers.CharField(required=False, default='', help_text='Programming language.')
    filename = serializers.CharField(required=False, default='', help_text='Filename for context.')
    instructions = serializers.CharField(
        required=False, default='', help_text='Custom instructions (required for custom action).',
    )

    def validate(self, attrs):
        if attrs['action'] == 'custom' and not attrs.get('instructions'):
            raise serializers.ValidationError(
                {'instructions': 'Instructions are required for custom actions.'}
            )
        return attrs

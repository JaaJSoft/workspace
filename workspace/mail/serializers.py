from rest_framework import serializers

from .models import MailAccount, MailAttachment, MailExtraction, MailFolder, MailLabel, MailMessage, MailRule, MailRuleLog
from .services.rules.schema import (
    SchemaError,
    parse_actions,
    parse_conditions,
    validate_tree_limits,
)


class MailAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailAccount
        fields = [
            'uuid', 'email', 'display_name', 'auth_method',
            'imap_host', 'imap_port', 'imap_use_ssl',
            'smtp_host', 'smtp_port', 'smtp_use_tls',
            'username', 'is_active',
            'last_sync_at', 'last_sync_error',
            'created_at', 'updated_at',
        ]


class MailAccountCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    display_name = serializers.CharField(max_length=255, required=False, default='', allow_blank=True)
    imap_host = serializers.CharField(max_length=255)
    imap_port = serializers.IntegerField(required=False, default=993)
    imap_use_ssl = serializers.BooleanField(required=False, default=True)
    smtp_host = serializers.CharField(max_length=255)
    smtp_port = serializers.IntegerField(required=False, default=587)
    smtp_use_tls = serializers.BooleanField(required=False, default=True)
    username = serializers.CharField(max_length=255)
    password = serializers.CharField(write_only=True)


class MailAccountUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    imap_host = serializers.CharField(max_length=255, required=False)
    imap_port = serializers.IntegerField(required=False)
    imap_use_ssl = serializers.BooleanField(required=False)
    smtp_host = serializers.CharField(max_length=255, required=False)
    smtp_port = serializers.IntegerField(required=False)
    smtp_use_tls = serializers.BooleanField(required=False)
    username = serializers.CharField(max_length=255, required=False)
    password = serializers.CharField(write_only=True, required=False)
    is_active = serializers.BooleanField(required=False)


class MailLabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailLabel
        fields = ['uuid', 'account_id', 'name', 'color', 'icon', 'position', 'unread_count']
        read_only_fields = ['uuid', 'account_id', 'unread_count']


class MailLabelCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    name = serializers.CharField(max_length=100)
    color = serializers.CharField(max_length=30, required=False, default='')
    icon = serializers.CharField(max_length=50, required=False, default='')


class MailLabelUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    color = serializers.CharField(max_length=30, required=False)
    icon = serializers.CharField(max_length=50, required=False)
    position = serializers.IntegerField(min_value=0, required=False)


class MailFolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailFolder
        fields = [
            'uuid', 'account_id', 'name', 'display_name', 'folder_type',
            'icon', 'color', 'is_hidden', 'message_count', 'unread_count',
        ]


class MailFolderCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    parent_name = serializers.CharField(max_length=255, required=False, default='', allow_blank=True)


class MailFolderUpdateSerializer(serializers.Serializer):
    icon = serializers.CharField(max_length=50, required=False, allow_null=True, allow_blank=True)
    color = serializers.CharField(max_length=30, required=False, allow_null=True, allow_blank=True)
    display_name = serializers.CharField(max_length=255, required=False)
    parent_name = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    is_hidden = serializers.BooleanField(required=False)


class MailAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailAttachment
        fields = ['uuid', 'filename', 'content_type', 'size', 'content_id', 'is_inline']


class MailMessageListSerializer(serializers.ModelSerializer):
    attachments_count = serializers.IntegerField(read_only=True)
    labels = serializers.SerializerMethodField()

    class Meta:
        model = MailMessage
        fields = [
            'uuid', 'account_id', 'folder_id', 'message_id', 'subject',
            'from_address', 'to_addresses', 'date', 'snippet',
            'is_read', 'is_starred', 'is_draft', 'has_attachments',
            'has_calendar_event', 'attachments_count', 'labels',
        ]

    def get_labels(self, obj):
        # Django serves from prefetch cache when message_labels is prefetched
        return [
            {'uuid': str(link.label.uuid), 'name': link.label.name, 'color': link.label.color}
            for link in obj.message_labels.all()
        ]


class _ExtractionTargetEventSerializer(serializers.Serializer):
    """Minimal embed of a calendar.Event inside a MailExtraction payload.
    Defined here to avoid a circular import from calendar.serializers."""
    uuid = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    start = serializers.DateTimeField(read_only=True)
    end = serializers.DateTimeField(read_only=True, allow_null=True)
    all_day = serializers.BooleanField(read_only=True)
    location = serializers.CharField(read_only=True)


class MailExtractionSerializer(serializers.ModelSerializer):
    target = serializers.SerializerMethodField()

    class Meta:
        model = MailExtraction
        fields = ['uuid', 'kind', 'target']

    def get_target(self, obj):
        target = obj.target
        if target is None:
            return None
        if obj.kind == MailExtraction.Kind.EVENT:
            return _ExtractionTargetEventSerializer(target).data
        return None


class MailMessageDetailSerializer(serializers.ModelSerializer):
    attachments = MailAttachmentSerializer(many=True, read_only=True)
    labels = serializers.SerializerMethodField()
    ai_summary_html = serializers.SerializerMethodField()
    extractions = serializers.SerializerMethodField()

    class Meta:
        model = MailMessage
        fields = [
            'uuid', 'account_id', 'folder_id', 'message_id', 'imap_uid',
            'subject', 'from_address', 'to_addresses', 'cc_addresses',
            'bcc_addresses', 'reply_to', 'date', 'snippet',
            'body_text', 'body_html',
            'is_read', 'is_starred', 'is_draft', 'has_attachments',
            'has_calendar_event', 'ai_summary', 'ai_summary_html',
            'attachments', 'labels', 'extractions', 'created_at',
        ]

    def get_ai_summary_html(self, obj):
        if obj.ai_summary:
            import mistune
            return mistune.html(obj.ai_summary)
        return None

    def get_labels(self, obj):
        # Django serves from prefetch cache when message_labels is prefetched
        return [
            {'uuid': str(link.label.uuid), 'name': link.label.name, 'color': link.label.color}
            for link in obj.message_labels.all()
        ]

    def get_extractions(self, obj):
        qs = obj.extractions.filter(
            status=MailExtraction.Status.DETECTED,
        ).select_related('target_content_type')
        return MailExtractionSerializer(qs, many=True).data


class MailMessageUpdateSerializer(serializers.Serializer):
    is_read = serializers.BooleanField(required=False)
    is_starred = serializers.BooleanField(required=False)
    ai_summary = serializers.CharField(required=False, allow_blank=True)


class SendEmailSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    to = serializers.ListField(child=serializers.EmailField(), min_length=1)
    cc = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    bcc = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    subject = serializers.CharField(max_length=1000, required=False, default='', allow_blank=True)
    body_html = serializers.CharField(required=False, default='', allow_blank=True)
    body_text = serializers.CharField(required=False, default='', allow_blank=True)
    reply_to = serializers.CharField(max_length=255, required=False, default='', allow_blank=True)
    workspace_file_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class DraftSaveSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    draft_id = serializers.UUIDField(required=False)
    to = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    cc = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    bcc = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    subject = serializers.CharField(max_length=1000, required=False, default='', allow_blank=True)
    body_html = serializers.CharField(required=False, default='', allow_blank=True)
    body_text = serializers.CharField(required=False, default='', allow_blank=True)
    reply_to = serializers.CharField(max_length=255, required=False, default='', allow_blank=True)


class MailLabelAssignSerializer(serializers.Serializer):
    label_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )


class BatchActionSerializer(serializers.Serializer):
    message_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    action = serializers.ChoiceField(choices=['mark_read', 'mark_unread', 'star', 'unstar', 'delete', 'move'])
    target_folder_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs['action'] == 'move' and not attrs.get('target_folder_id'):
            raise serializers.ValidationError({'target_folder_id': 'This field is required for move action.'})
        return attrs


def _validate_conditions(value):
    # Catch SchemaError and surface a clean, user-facing message rather than
    # the raw Pydantic exception text (which would expose internal validator
    # paths and is flagged by CodeQL as information exposure).
    try:
        node = parse_conditions(value)
        validate_tree_limits(node)
    except SchemaError:
        raise serializers.ValidationError(
            'Invalid conditions: check field, op and value formats, and that '
            'the tree depth and total leaf count are within limits.'
        )
    return value


def _validate_actions(value):
    try:
        parsed = parse_actions(value)
    except SchemaError:
        raise serializers.ValidationError(
            'Invalid actions: check that each action has a valid type and '
            'the required label_id or folder_id when applicable.'
        )
    if not parsed:
        raise serializers.ValidationError('at least one action is required')
    return value


class MailRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailRule
        fields = [
            'uuid', 'account_id', 'name', 'is_enabled', 'position',
            'stop_processing', 'conditions', 'actions',
            'last_matched_at', 'match_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'uuid', 'account_id', 'last_matched_at', 'match_count',
            'created_at', 'updated_at',
        ]


class MailRuleCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    name = serializers.CharField(max_length=120)
    is_enabled = serializers.BooleanField(required=False, default=True)
    stop_processing = serializers.BooleanField(required=False, default=False)
    position = serializers.IntegerField(min_value=0, required=False, default=0)
    conditions = serializers.JSONField(validators=[_validate_conditions])
    actions = serializers.JSONField(validators=[_validate_actions])


class MailRuleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    is_enabled = serializers.BooleanField(required=False)
    stop_processing = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, required=False)
    conditions = serializers.JSONField(required=False, validators=[_validate_conditions])
    actions = serializers.JSONField(required=False, validators=[_validate_actions])


class MailRuleReorderSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=0)


class MailRuleTestSerializer(serializers.Serializer):
    message_id = serializers.UUIDField()
    rule_id = serializers.UUIDField(required=False)
    conditions = serializers.JSONField(required=False, validators=[_validate_conditions])

    def validate(self, attrs):
        if 'rule_id' not in attrs and 'conditions' not in attrs:
            raise serializers.ValidationError('rule_id or conditions is required')
        return attrs


class MailRuleLogSerializer(serializers.ModelSerializer):
    message_subject = serializers.CharField(source='message.subject', read_only=True)

    class Meta:
        model = MailRuleLog
        fields = [
            'uuid', 'rule_name_snapshot', 'message', 'message_subject',
            'actions_applied', 'created_at',
        ]

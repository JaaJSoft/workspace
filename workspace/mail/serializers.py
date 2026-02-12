from rest_framework import serializers

from .models import MailAccount, MailAttachment, MailFolder, MailMessage


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


class MailFolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailFolder
        fields = [
            'uuid', 'account_id', 'name', 'display_name', 'folder_type',
            'icon', 'color', 'message_count', 'unread_count',
        ]


class MailFolderCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)


class MailFolderUpdateSerializer(serializers.Serializer):
    icon = serializers.CharField(max_length=50, required=False, allow_null=True, allow_blank=True)
    color = serializers.CharField(max_length=30, required=False, allow_null=True, allow_blank=True)
    display_name = serializers.CharField(max_length=255, required=False)


class MailAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailAttachment
        fields = ['uuid', 'filename', 'content_type', 'size', 'content_id', 'is_inline']


class MailMessageListSerializer(serializers.ModelSerializer):
    attachments_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = MailMessage
        fields = [
            'uuid', 'folder_id', 'message_id', 'subject',
            'from_address', 'to_addresses', 'date', 'snippet',
            'is_read', 'is_starred', 'is_draft', 'has_attachments',
            'attachments_count',
        ]


class MailMessageDetailSerializer(serializers.ModelSerializer):
    attachments = MailAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = MailMessage
        fields = [
            'uuid', 'account_id', 'folder_id', 'message_id', 'imap_uid',
            'subject', 'from_address', 'to_addresses', 'cc_addresses',
            'bcc_addresses', 'reply_to', 'date', 'snippet',
            'body_text', 'body_html',
            'is_read', 'is_starred', 'is_draft', 'has_attachments',
            'attachments', 'created_at',
        ]


class MailMessageUpdateSerializer(serializers.Serializer):
    is_read = serializers.BooleanField(required=False)
    is_starred = serializers.BooleanField(required=False)


class SendEmailSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    to = serializers.ListField(child=serializers.EmailField(), min_length=1)
    cc = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    bcc = serializers.ListField(child=serializers.EmailField(), required=False, default=list)
    subject = serializers.CharField(max_length=1000, required=False, default='', allow_blank=True)
    body_html = serializers.CharField(required=False, default='', allow_blank=True)
    body_text = serializers.CharField(required=False, default='', allow_blank=True)
    reply_to = serializers.CharField(max_length=255, required=False, default='', allow_blank=True)


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


class BatchActionSerializer(serializers.Serializer):
    message_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    action = serializers.ChoiceField(choices=['mark_read', 'mark_unread', 'star', 'unstar', 'delete'])

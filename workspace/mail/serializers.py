from datetime import UTC

import mistune
from django.utils.translation import gettext as _
from rest_framework import serializers

from workspace.common.url_guard import UnsafeUrl, check_remote_host

from .models import (
    MailAccount,
    MailAttachment,
    MailExtraction,
    MailFolder,
    MailLabel,
    MailMessage,
    MailRule,
    MailRuleLog,
)
from .services.imap_parse import sanitize_mail_html
from .services.inline_images import InlineImages
from .services.network_policy import mail_network_policy
from .services.remote_images import has_remote_images
from .services.rules.schema import (
    SchemaError,
    parse_actions,
    parse_conditions,
    validate_tree_limits,
)
from .services.trusted_senders import is_trusted_sender

# escape=True so raw HTML in the AI summary is escaped instead of executed when
# the rendered string is injected via Alpine x-html. mistune.html() uses escape=False.
_md = mistune.create_markdown(
    escape=True,
    plugins=["strikethrough", "footnotes", "table", "speedup"],
)


class MailAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailAccount
        fields = [
            "uuid",
            "email",
            "display_name",
            "signature",
            "auth_method",
            "imap_host",
            "imap_port",
            "imap_use_ssl",
            "smtp_host",
            "smtp_port",
            "smtp_use_tls",
            "username",
            "is_active",
            "last_sync_at",
            "last_sync_error",
            "archives_in_all_mail",
            "created_at",
            "updated_at",
        ]


class _PortField(serializers.IntegerField):
    def __init__(self, **kwargs):
        super().__init__(required=False, min_value=1, max_value=65535, **kwargs)


class _MailHostsMixin:
    """Refuses a server mail may not connect to. A name passes here and is
    resolved and checked each time a connection is opened."""

    def _allowed(self, host):
        try:
            check_remote_host(host, mail_network_policy())
        except UnsafeUrl as exc:
            raise serializers.ValidationError(exc.user_message) from None
        return host

    def validate_imap_host(self, value):
        return self._allowed(value)

    def validate_smtp_host(self, value):
        return self._allowed(value)


class MailAccountCreateSerializer(_MailHostsMixin, serializers.Serializer):
    email = serializers.EmailField()
    display_name = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    imap_host = serializers.CharField(max_length=255)
    imap_port = _PortField(default=993)
    imap_use_ssl = serializers.BooleanField(required=False, default=True)
    smtp_host = serializers.CharField(max_length=255)
    smtp_port = _PortField(default=587)
    smtp_use_tls = serializers.BooleanField(required=False, default=True)
    username = serializers.CharField(max_length=255)
    password = serializers.CharField(write_only=True)


class MailAccountUpdateSerializer(_MailHostsMixin, serializers.Serializer):
    display_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    signature = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    imap_host = serializers.CharField(max_length=255, required=False)
    imap_port = _PortField()
    imap_use_ssl = serializers.BooleanField(required=False)
    smtp_host = serializers.CharField(max_length=255, required=False)
    smtp_port = _PortField()
    smtp_use_tls = serializers.BooleanField(required=False)
    username = serializers.CharField(max_length=255, required=False)
    password = serializers.CharField(write_only=True, required=False)
    is_active = serializers.BooleanField(required=False)


class MailLabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailLabel
        fields = [
            "uuid",
            "account_id",
            "name",
            "description",
            "color",
            "icon",
            "position",
            "notify_on_apply",
            "unread_count",
        ]
        read_only_fields = ["uuid", "account_id", "unread_count"]


class MailLabelCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )
    color = serializers.CharField(max_length=30, required=False, default="")
    icon = serializers.CharField(max_length=50, required=False, default="")
    notify_on_apply = serializers.BooleanField(required=False, default=False)


class MailLabelUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(
        max_length=200, required=False, allow_blank=True
    )
    color = serializers.CharField(max_length=30, required=False)
    icon = serializers.CharField(max_length=50, required=False)
    position = serializers.IntegerField(min_value=0, required=False)
    notify_on_apply = serializers.BooleanField(required=False)


class MailFolderSerializer(serializers.ModelSerializer):
    alias_of = serializers.PrimaryKeyRelatedField(read_only=True)
    aliases = serializers.SerializerMethodField()
    # Counters are stored per physical folder; the API speaks in logical
    # folders, so the group total is summed here rather than denormalized
    # onto the canonical row. Callers must prefetch_related("aliases").
    message_count = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = MailFolder
        fields = [
            "uuid",
            "account_id",
            "name",
            "display_name",
            "folder_type",
            "icon",
            "color",
            "is_hidden",
            "ai_classify_disabled",
            "alias_of",
            "aliases",
            "message_count",
            "unread_count",
        ]

    def get_aliases(self, obj):
        return [
            {"uuid": str(alias.uuid), "display_name": alias.display_name}
            for alias in obj.aliases.all()
        ]

    def get_message_count(self, obj):
        return obj.message_count + sum(a.message_count for a in obj.aliases.all())

    def get_unread_count(self, obj):
        return obj.unread_count + sum(a.unread_count for a in obj.aliases.all())


class MailFolderCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    parent_name = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )


class MailFolderUpdateSerializer(serializers.Serializer):
    icon = serializers.CharField(
        max_length=50, required=False, allow_null=True, allow_blank=True
    )
    color = serializers.CharField(
        max_length=30, required=False, allow_null=True, allow_blank=True
    )
    display_name = serializers.CharField(max_length=255, required=False)
    parent_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, allow_null=True
    )
    is_hidden = serializers.BooleanField(required=False)
    ai_classify_disabled = serializers.BooleanField(required=False)


class MailFolderMergeSerializer(serializers.Serializer):
    into = serializers.UUIDField()


class MailAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailAttachment
        fields = ["uuid", "filename", "content_type", "size", "content_id", "is_inline"]


class _ComposeAttachmentSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    filename = serializers.CharField()
    size = serializers.IntegerField()


class ComposePrefillSerializer(serializers.Serializer):
    """A `services.compose.Prefill`, as the compose dialog opens it."""

    account_id = serializers.UUIDField(source="account.uuid")
    to = serializers.ListField(child=serializers.CharField())
    cc = serializers.ListField(child=serializers.CharField())
    subject = serializers.CharField()
    body_text = serializers.CharField(trim_whitespace=False)
    signature_block = serializers.CharField(trim_whitespace=False)
    reply_message_id = serializers.UUIDField(allow_null=True)
    attachments = _ComposeAttachmentSerializer(many=True)


class MailMessageListSerializer(serializers.ModelSerializer):
    labels = serializers.SerializerMethodField()
    # The {name, email} shape is an API contract (mail UI JS reads it),
    # not a stored field - the model only has from_name/from_email.
    from_address = serializers.SerializerMethodField()

    def get_from_address(self, obj):
        return {"name": obj.from_name, "email": obj.from_email}

    class Meta:
        model = MailMessage
        fields = [
            "uuid",
            "account_id",
            "folder_id",
            "message_id",
            "subject",
            "from_address",
            "to_addresses",
            "date",
            "snippet",
            "is_read",
            "is_starred",
            "is_answered",
            "is_draft",
            "has_attachments",
            "has_calendar_event",
            "labels",
        ]

    def get_labels(self, obj):
        # Django serves from prefetch cache when message_labels is prefetched
        return [
            {
                "uuid": str(link.label.uuid),
                "name": link.label.name,
                "color": link.label.color,
            }
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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.all_day:
            # Same contract as calendar's EventSerializer: an all-day value
            # is a UTC-midnight day label, sent date-only so no reader's
            # zone can move it to the previous day.
            data["start"] = instance.start.astimezone(UTC).date().isoformat()
            if instance.end:
                data["end"] = instance.end.astimezone(UTC).date().isoformat()
        return data


class MailExtractionSerializer(serializers.ModelSerializer):
    target = serializers.SerializerMethodField()

    class Meta:
        model = MailExtraction
        fields = ["uuid", "kind", "target"]

    def get_target(self, obj):
        target = obj.target
        if target is None:
            return None
        if obj.kind == MailExtraction.Kind.EVENT:
            return _ExtractionTargetEventSerializer(target).data
        return None


class MailTrustedSenderSerializer(serializers.Serializer):
    email = serializers.CharField(max_length=254)


class MailMessageDetailSerializer(serializers.ModelSerializer):
    # Each with ``embedded``: whether the body shows it as a picture.
    attachments = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    ai_summary_html = serializers.SerializerMethodField()
    extractions = serializers.SerializerMethodField()
    # Same {name, email} API contract as the list serializer.
    from_address = serializers.SerializerMethodField()
    # Cleaned again on the way out, so a body stored before the allow-list
    # last changed, or written by the composer, renders under today's rules.
    body_html = serializers.SerializerMethodField()
    has_remote_images = serializers.SerializerMethodField()
    sender_trusted = serializers.SerializerMethodField()

    def get_from_address(self, obj):
        return {"name": obj.from_name, "email": obj.from_email}

    def _rendered_body(self, obj):
        """The body to display, its cid: pictures pointing at their parts.

        Memoized on the instance: the body and the attachment list both read it.
        """
        if not hasattr(obj, "_rendered_body"):
            inline_images = InlineImages(obj.attachments.all())
            html = sanitize_mail_html(
                obj.body_html, attribute_filter=inline_images.resolve
            )
            obj._rendered_body = (html, inline_images.embedded)
        return obj._rendered_body

    def get_body_html(self, obj):
        return self._rendered_body(obj)[0]

    def get_attachments(self, obj):
        embedded = {str(uuid) for uuid in self._rendered_body(obj)[1]}
        return [
            {**data, "embedded": data["uuid"] in embedded}
            for data in MailAttachmentSerializer(obj.attachments.all(), many=True).data
        ]

    def get_has_remote_images(self, obj):
        return has_remote_images(obj.body_html)

    def get_sender_trusted(self, obj):
        return is_trusted_sender(obj.account.owner_id, obj.from_email)

    class Meta:
        model = MailMessage
        fields = [
            "uuid",
            "account_id",
            "folder_id",
            "message_id",
            "imap_uid",
            "subject",
            "from_address",
            "to_addresses",
            "cc_addresses",
            "bcc_addresses",
            "reply_to_addresses",
            "date",
            "snippet",
            "body_text",
            "body_html",
            "has_remote_images",
            "sender_trusted",
            "is_read",
            "is_starred",
            "is_answered",
            "is_draft",
            "has_attachments",
            "has_calendar_event",
            "ai_summary",
            "ai_summary_html",
            "attachments",
            "labels",
            "extractions",
            "created_at",
        ]

    def get_ai_summary_html(self, obj):
        if obj.ai_summary:
            return _md(obj.ai_summary)
        return None

    def get_labels(self, obj):
        # Django serves from prefetch cache when message_labels is prefetched
        return [
            {
                "uuid": str(link.label.uuid),
                "name": link.label.name,
                "color": link.label.color,
            }
            for link in obj.message_labels.all()
        ]

    def get_extractions(self, obj):
        qs = obj.extractions.filter(
            status=MailExtraction.Status.DETECTED,
        ).select_related("target_content_type")
        return MailExtractionSerializer(qs, many=True).data


class MailMessageUpdateSerializer(serializers.Serializer):
    is_read = serializers.BooleanField(required=False)
    is_starred = serializers.BooleanField(required=False)
    ai_summary = serializers.CharField(required=False, allow_blank=True)


class SendEmailSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    to = serializers.ListField(child=serializers.EmailField(), min_length=1)
    cc = serializers.ListField(
        child=serializers.EmailField(), required=False, default=list
    )
    bcc = serializers.ListField(
        child=serializers.EmailField(), required=False, default=list
    )
    subject = serializers.CharField(
        max_length=1000, required=False, default="", allow_blank=True
    )
    body_html = serializers.CharField(required=False, default="", allow_blank=True)
    body_text = serializers.CharField(required=False, default="", allow_blank=True)
    reply_to = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    file_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    # Attachments of stored messages (a forward carries the original's).
    attachment_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    # UUID of the message being replied to. The In-Reply-To / References
    # headers are derived from it server-side; `reply_to` above is the
    # unrelated Reply-To header.
    reply_message_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )


class DraftSaveSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    draft_id = serializers.UUIDField(required=False)
    to = serializers.ListField(
        child=serializers.EmailField(), required=False, default=list
    )
    cc = serializers.ListField(
        child=serializers.EmailField(), required=False, default=list
    )
    bcc = serializers.ListField(
        child=serializers.EmailField(), required=False, default=list
    )
    subject = serializers.CharField(
        max_length=1000, required=False, default="", allow_blank=True
    )
    body_html = serializers.CharField(required=False, default="", allow_blank=True)
    body_text = serializers.CharField(required=False, default="", allow_blank=True)
    reply_to = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    reply_message_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    attachment_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class MailLabelAssignSerializer(serializers.Serializer):
    label_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )


class BatchActionSerializer(serializers.Serializer):
    message_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    action = serializers.ChoiceField(
        choices=[
            "mark_read",
            "mark_unread",
            "star",
            "unstar",
            "delete",
            "move",
            "archive",
            "spam",
            "not_spam",
        ]
    )
    target_folder_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs["action"] == "move" and not attrs.get("target_folder_id"):
            raise serializers.ValidationError(
                {"target_folder_id": "This field is required for move action."}
            )
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
            _(
                "Invalid conditions: check field, op and value formats, and that "
                "the tree depth and total leaf count are within limits."
            )
        ) from None
    return value


def _validate_actions(value):
    try:
        parsed = parse_actions(value)
    except SchemaError:
        raise serializers.ValidationError(
            _(
                "Invalid actions: check that each action has a valid type and "
                "the required label_id or folder_id when applicable."
            )
        ) from None
    if not parsed:
        raise serializers.ValidationError(_("at least one action is required"))
    return value


class MailRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailRule
        fields = [
            "uuid",
            "account_id",
            "name",
            "is_enabled",
            "position",
            "stop_processing",
            "conditions",
            "actions",
            "last_matched_at",
            "match_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "account_id",
            "last_matched_at",
            "match_count",
            "created_at",
            "updated_at",
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
    conditions = serializers.JSONField(
        required=False, validators=[_validate_conditions]
    )
    actions = serializers.JSONField(required=False, validators=[_validate_actions])


class MailRuleReorderSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=0)


class MailRuleApplySerializer(serializers.Serializer):
    folder_id = serializers.UUIDField()
    dry_run = serializers.BooleanField(required=False, default=False)


class MailRuleTestSerializer(serializers.Serializer):
    message_id = serializers.UUIDField()
    rule_id = serializers.UUIDField(required=False)
    conditions = serializers.JSONField(
        required=False, validators=[_validate_conditions]
    )

    def validate(self, attrs):
        if "rule_id" not in attrs and "conditions" not in attrs:
            raise serializers.ValidationError("rule_id or conditions is required")
        return attrs


class MailRuleLogSerializer(serializers.ModelSerializer):
    message_subject = serializers.CharField(source="message.subject", read_only=True)

    class Meta:
        model = MailRuleLog
        fields = [
            "uuid",
            "rule_name_snapshot",
            "message",
            "message_subject",
            "actions_applied",
            "created_at",
        ]

from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.decorators import display

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
from .services.imap_sync import queue_account_syncs
from .services.rules.management import set_rules_enabled


@admin.register(MailAccount)
class MailAccountAdmin(ModelAdmin):
    list_display = (
        "email",
        "owner",
        "display_name",
        "is_active",
        "sync_health",
        "last_sync_at",
        "created_at",
    )
    list_filter = ("is_active", "auth_method")
    list_select_related = ("owner",)
    search_fields = ("email", "display_name", "owner__username")
    autocomplete_fields = ("owner",)
    actions = ("sync_now",)

    @display(
        description="Sync",
        label={"error": "danger", "ok": "success", "never": "info"},
    )
    def sync_health(self, obj):
        if obj.last_sync_error:
            return "error"
        if obj.last_sync_at is None:
            return "never"
        return "ok"

    @admin.action(description="Sync now")
    def sync_now(self, request, queryset):
        count = queue_account_syncs(queryset)
        self.message_user(
            request,
            f"Sync queued for {count} active account(s).",
            messages.SUCCESS,
        )


@admin.register(MailFolder)
class MailFolderAdmin(ModelAdmin):
    list_display = (
        "display_name",
        "account",
        "folder_type",
        "message_count",
        "unread_count",
    )
    list_filter = ("folder_type",)
    list_select_related = ("account",)
    search_fields = ("name", "display_name", "account__email")
    autocomplete_fields = ("account",)


@admin.register(MailLabel)
class MailLabelAdmin(ModelAdmin):
    list_display = (
        "name",
        "account",
        "color",
        "position",
        "unread_count",
        "notify_on_apply",
    )
    list_filter = ("notify_on_apply",)
    list_select_related = ("account",)
    search_fields = ("name", "account__email")
    autocomplete_fields = ("account",)


class MailAttachmentInline(TabularInline):
    model = MailAttachment
    extra = 0


@admin.register(MailMessage)
class MailMessageAdmin(ModelAdmin):
    list_display = ("subject", "account", "folder", "date", "is_read", "is_starred")
    list_filter = (
        "is_read",
        "is_starred",
        "is_draft",
        ("date", RangeDateTimeFilter),
    )
    list_filter_submit = True
    list_select_related = ("account", "folder")
    search_fields = ("uuid", "subject", "snippet")
    autocomplete_fields = ("account", "folder")
    date_hierarchy = "date"
    inlines = [MailAttachmentInline]


@admin.register(MailAttachment)
class MailAttachmentAdmin(ModelAdmin):
    list_display = ("filename", "message", "content_type", "size", "is_inline")
    list_select_related = ("message",)
    search_fields = ("filename", "message__subject")
    autocomplete_fields = ("message",)


@admin.register(MailExtraction)
class MailExtractionAdmin(ModelAdmin):
    list_display = (
        "kind",
        "status_badge",
        "mail_message",
        "confidence",
        "model_used",
        "created_at",
    )
    list_filter = ("kind", "status")
    list_select_related = ("mail_message",)
    search_fields = ("mail_message__subject", "model_used")
    date_hierarchy = "created_at"

    @display(
        description="Status",
        label={
            MailExtraction.Status.DETECTED: "info",
        },
    )
    def status_badge(self, obj):
        return obj.status

    # Extraction rows are an audit trail of what the LLM produced; they are
    # written by the extractor only. Deletion stays open for cleanup.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MailRule)
class MailRuleAdmin(ModelAdmin):
    list_display = (
        "name",
        "account",
        "is_enabled",
        "position",
        "match_count",
        "last_matched_at",
    )
    list_filter = ("is_enabled", "stop_processing")
    list_select_related = ("account",)
    search_fields = ("name", "account__email")
    autocomplete_fields = ("account",)
    readonly_fields = ("match_count", "last_matched_at", "created_at", "updated_at")
    actions = ("enable_rules", "disable_rules")

    @admin.action(description="Enable selected rules")
    def enable_rules(self, request, queryset):
        count = set_rules_enabled(queryset, True)
        self.message_user(request, f"{count} rule(s) enabled.", messages.SUCCESS)

    @admin.action(description="Disable selected rules")
    def disable_rules(self, request, queryset):
        count = set_rules_enabled(queryset, False)
        self.message_user(request, f"{count} rule(s) disabled.", messages.SUCCESS)


@admin.register(MailRuleLog)
class MailRuleLogAdmin(ModelAdmin):
    list_display = ("uuid", "rule_name_snapshot", "message", "created_at")
    list_select_related = ("message",)
    readonly_fields = (
        "rule",
        "rule_name_snapshot",
        "message",
        "actions_applied",
        "created_at",
    )
    search_fields = ("rule_name_snapshot",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

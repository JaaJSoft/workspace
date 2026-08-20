from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
    MailRule,
    MailRuleLog,
)


@admin.register(MailAccount)
class MailAccountAdmin(ModelAdmin):
    list_display = (
        "email",
        "owner",
        "display_name",
        "is_active",
        "last_sync_at",
        "created_at",
    )
    list_filter = ("is_active", "auth_method")
    search_fields = ("email", "display_name")


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
    search_fields = ("name", "display_name")


class MailAttachmentInline(TabularInline):
    model = MailAttachment
    extra = 0


@admin.register(MailMessage)
class MailMessageAdmin(ModelAdmin):
    list_display = ("subject", "account", "folder", "date", "is_read", "is_starred")
    list_filter = ("is_read", "is_starred", "is_draft")
    search_fields = ("subject", "snippet")
    inlines = [MailAttachmentInline]


@admin.register(MailAttachment)
class MailAttachmentAdmin(ModelAdmin):
    list_display = ("filename", "message", "content_type", "size", "is_inline")


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
    search_fields = ("name", "account__email")
    readonly_fields = ("match_count", "last_matched_at", "created_at", "updated_at")


@admin.register(MailRuleLog)
class MailRuleLogAdmin(ModelAdmin):
    list_display = ("uuid", "rule_name_snapshot", "message", "created_at")
    readonly_fields = (
        "rule",
        "rule_name_snapshot",
        "message",
        "actions_applied",
        "created_at",
    )
    search_fields = ("rule_name_snapshot",)

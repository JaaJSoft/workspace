from django.contrib import admin
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.filters.admin import RangeDateTimeFilter

from .models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    PinnedConversation,
    PinnedMessage,
    Reaction,
)


class ConversationMemberInline(TabularInline):
    model = ConversationMember
    extra = 0
    readonly_fields = ("uuid", "joined_at")


class ConversationSummaryInline(StackedInline):
    from workspace.ai.models import ConversationSummary

    model = ConversationSummary
    extra = 0
    max_num = 1
    readonly_fields = ("content", "up_to", "updated_at")
    verbose_name = "AI summary"
    verbose_name_plural = "AI summary"


@admin.register(Conversation)
class ConversationAdmin(ModelAdmin):
    list_display = ("uuid", "kind", "title", "created_by", "created_at", "updated_at")
    list_filter = ("kind",)
    list_select_related = ("created_by",)
    search_fields = ("uuid", "title", "description", "created_by__username")
    date_hierarchy = "created_at"
    inlines = [ConversationMemberInline, ConversationSummaryInline]


class MessageAttachmentInline(TabularInline):
    model = MessageAttachment
    extra = 0
    readonly_fields = ("uuid", "original_name", "mime_type", "size", "created_at")


@admin.register(Message)
class MessageAdmin(ModelAdmin):
    list_display = (
        "uuid",
        "conversation",
        "author",
        "created_at",
        "edited_at",
        "deleted_at",
    )
    list_filter = ("deleted_at", ("created_at", RangeDateTimeFilter))
    list_filter_submit = True
    list_select_related = ("conversation", "author")
    search_fields = ("uuid", "body", "author__username")
    autocomplete_fields = ("conversation", "author")
    date_hierarchy = "created_at"
    inlines = [MessageAttachmentInline]


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(ModelAdmin):
    list_display = (
        "uuid",
        "message",
        "original_name",
        "mime_type",
        "size",
        "created_at",
    )
    list_select_related = ("message",)
    search_fields = ("original_name",)
    autocomplete_fields = ("message",)


@admin.register(Reaction)
class ReactionAdmin(ModelAdmin):
    list_display = ("uuid", "message", "user", "emoji", "created_at")
    list_select_related = ("message", "user")
    search_fields = ("emoji", "user__username")
    autocomplete_fields = ("message", "user")


@admin.register(ConversationMember)
class ConversationMemberAdmin(ModelAdmin):
    list_display = (
        "uuid",
        "conversation",
        "user",
        "joined_at",
        "left_at",
        "last_read_at",
    )
    list_filter = ("left_at",)
    list_select_related = ("conversation", "user")
    search_fields = ("user__username", "conversation__title")
    autocomplete_fields = ("conversation", "user")


@admin.register(PinnedMessage)
class PinnedMessageAdmin(ModelAdmin):
    list_display = ("uuid", "conversation", "message", "pinned_by", "created_at")
    list_select_related = ("conversation", "message", "pinned_by")
    search_fields = ("pinned_by__username", "conversation__title")
    autocomplete_fields = ("conversation", "message", "pinned_by")


@admin.register(PinnedConversation)
class PinnedConversationAdmin(ModelAdmin):
    list_display = ("uuid", "owner", "conversation", "position", "created_at")
    list_select_related = ("owner", "conversation")
    search_fields = ("owner__username", "conversation__title")
    autocomplete_fields = ("owner", "conversation")

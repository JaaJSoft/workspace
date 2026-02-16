from django.contrib import admin

from .models import (
    Conversation, ConversationMember, Message, MessageAttachment,
    PinnedConversation, PinnedMessage, Reaction,
)


class ConversationMemberInline(admin.TabularInline):
    model = ConversationMember
    extra = 0
    readonly_fields = ('uuid', 'joined_at')


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'kind', 'title', 'created_by', 'created_at', 'updated_at')
    list_filter = ('kind',)
    search_fields = ('title', 'description', 'created_by__username')
    inlines = [ConversationMemberInline]


class MessageAttachmentInline(admin.TabularInline):
    model = MessageAttachment
    extra = 0
    readonly_fields = ('uuid', 'original_name', 'mime_type', 'size', 'created_at')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'conversation', 'author', 'created_at', 'edited_at', 'deleted_at')
    list_filter = ('deleted_at',)
    search_fields = ('body', 'author__username')
    inlines = [MessageAttachmentInline]


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'message', 'original_name', 'mime_type', 'size', 'created_at')
    search_fields = ('original_name',)


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'message', 'user', 'emoji', 'created_at')
    search_fields = ('emoji', 'user__username')


@admin.register(ConversationMember)
class ConversationMemberAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'conversation', 'user', 'joined_at', 'left_at', 'last_read_at')
    list_filter = ('left_at',)
    search_fields = ('user__username', 'conversation__title')
    raw_id_fields = ('conversation', 'user')


@admin.register(PinnedMessage)
class PinnedMessageAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'conversation', 'message', 'pinned_by', 'created_at')
    search_fields = ('pinned_by__username', 'conversation__title')
    raw_id_fields = ('conversation', 'message', 'pinned_by')


@admin.register(PinnedConversation)
class PinnedConversationAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'owner', 'conversation', 'position', 'created_at')
    search_fields = ('owner__username', 'conversation__title')
    raw_id_fields = ('owner', 'conversation')

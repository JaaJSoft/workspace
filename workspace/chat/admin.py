from django.contrib import admin

from .models import Conversation, ConversationMember, Message, Reaction


class ConversationMemberInline(admin.TabularInline):
    model = ConversationMember
    extra = 0
    readonly_fields = ('uuid', 'joined_at')


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'kind', 'title', 'created_by', 'created_at', 'updated_at')
    list_filter = ('kind',)
    search_fields = ('title', 'created_by__username')
    inlines = [ConversationMemberInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'conversation', 'author', 'created_at', 'edited_at', 'deleted_at')
    list_filter = ('deleted_at',)
    search_fields = ('body', 'author__username')


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'message', 'user', 'emoji', 'created_at')
    search_fields = ('emoji', 'user__username')

from django.contrib import admin

from .models import FileComment, FileShare, MimeTypeRule


@admin.register(MimeTypeRule)
class MimeTypeRuleAdmin(admin.ModelAdmin):
    list_display = ('pattern', 'priority', 'icon', 'color', 'category', 'viewer_type', 'is_wildcard')
    list_editable = ('priority', 'icon', 'color', 'category', 'viewer_type')
    list_filter = ('category', 'is_wildcard')
    search_fields = ('pattern',)
    ordering = ('priority', 'pattern')


@admin.register(FileComment)
class FileCommentAdmin(admin.ModelAdmin):
    list_display = ('file', 'author', 'body', 'created_at', 'edited_at', 'deleted_at')
    list_filter = ('created_at', 'deleted_at')
    search_fields = ('file__name', 'author__username', 'body')
    raw_id_fields = ('file', 'author')


@admin.register(FileShare)
class FileShareAdmin(admin.ModelAdmin):
    list_display = ('file', 'shared_by', 'shared_with', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('file__name', 'shared_by__username', 'shared_with__username')
    raw_id_fields = ('file', 'shared_by', 'shared_with')

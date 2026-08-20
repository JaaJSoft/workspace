from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    File,
    FileComment,
    FileFavorite,
    FileShare,
    PinnedFolder,
    ThumbnailFailure,
)


@admin.register(FileComment)
class FileCommentAdmin(ModelAdmin):
    list_display = ("file", "author", "body", "created_at", "edited_at", "deleted_at")
    list_filter = ("created_at", "deleted_at")
    search_fields = ("file__name", "author__username", "body")
    raw_id_fields = ("file", "author")


@admin.register(FileShare)
class FileShareAdmin(ModelAdmin):
    list_display = ("file", "shared_by", "shared_with", "created_at")
    list_filter = ("created_at",)
    search_fields = ("file__name", "shared_by__username", "shared_with__username")
    raw_id_fields = ("file", "shared_by", "shared_with")


@admin.register(File)
class FileAdmin(ModelAdmin):
    list_display = (
        "uuid",
        "name",
        "node_type",
        "owner",
        "size",
        "created_at",
        "deleted_at",
    )
    list_filter = ("node_type", "deleted_at")
    search_fields = ("name", "path", "owner__username")
    raw_id_fields = ("owner", "parent")
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(FileFavorite)
class FileFavoriteAdmin(ModelAdmin):
    list_display = ("uuid", "owner", "file", "created_at")
    search_fields = ("owner__username", "file__name")
    raw_id_fields = ("owner", "file")


@admin.register(PinnedFolder)
class PinnedFolderAdmin(ModelAdmin):
    list_display = ("uuid", "owner", "folder", "position", "created_at")
    search_fields = ("owner__username", "folder__name")
    raw_id_fields = ("owner", "folder")


@admin.register(ThumbnailFailure)
class ThumbnailFailureAdmin(ModelAdmin):
    """Read the recorded errors, and unpark a single file by deleting its row."""

    list_display = ("file", "attempts", "last_attempt_at", "last_error")
    list_filter = ("last_attempt_at",)
    list_select_related = ("file",)
    search_fields = ("file__name", "last_error")
    raw_id_fields = ("file",)

from django.contrib import admin, messages
from django.template.defaultfilters import filesizeformat
from unfold.admin import ModelAdmin, StackedInline
from unfold.contrib.filters.admin import RangeDateTimeFilter

from .models import (
    File,
    FileComment,
    FileFavorite,
    FileScan,
    FileShare,
    GroupStorageQuota,
    PinnedFolder,
    ThumbnailFailure,
    UserStorageQuota,
)
from .services.quota import (
    group_usage,
    group_usage_subquery,
    personal_usage,
    personal_usage_subquery,
)
from .services.thumbnails.failures import retry_failures


@admin.register(FileComment)
class FileCommentAdmin(ModelAdmin):
    list_display = ("file", "author", "body", "created_at", "edited_at", "deleted_at")
    list_filter = ("created_at", "deleted_at")
    list_select_related = ("file", "author")
    search_fields = ("file__name", "author__username", "body")
    autocomplete_fields = ("file", "author")


@admin.register(FileShare)
class FileShareAdmin(ModelAdmin):
    list_display = ("file", "shared_by", "shared_with", "created_at")
    list_filter = ("created_at",)
    list_select_related = ("file", "shared_by", "shared_with")
    search_fields = ("file__name", "shared_by__username", "shared_with__username")
    autocomplete_fields = ("file", "shared_by", "shared_with")


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
    list_filter = ("node_type", "deleted_at", ("created_at", RangeDateTimeFilter))
    list_filter_submit = True
    list_select_related = ("owner",)
    search_fields = ("uuid", "name", "path", "owner__username")
    autocomplete_fields = ("owner", "parent")
    readonly_fields = ("uuid", "created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(FileFavorite)
class FileFavoriteAdmin(ModelAdmin):
    list_display = ("uuid", "owner", "file", "created_at")
    list_select_related = ("owner", "file")
    search_fields = ("owner__username", "file__name")
    autocomplete_fields = ("owner", "file")


@admin.register(PinnedFolder)
class PinnedFolderAdmin(ModelAdmin):
    list_display = ("uuid", "owner", "folder", "position", "created_at")
    list_select_related = ("owner", "folder")
    search_fields = ("owner__username", "folder__name")
    autocomplete_fields = ("owner", "folder")


@admin.register(ThumbnailFailure)
class ThumbnailFailureAdmin(ModelAdmin):
    """Recorded thumbnail-generation errors; a file is unparked by deleting
    its row (the retry action does that and queues a generation pass)."""

    list_display = ("file", "attempts", "last_attempt_at", "last_error")
    list_filter = ("last_attempt_at",)
    list_select_related = ("file",)
    search_fields = ("file__name", "last_error")
    actions = ("retry_thumbnails",)

    # Rows are written by the thumbnail worker; there is nothing to author or
    # edit by hand. Deleting a row is the documented way to unpark a file.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Retry thumbnail generation", permissions=["delete"])
    def retry_thumbnails(self, request, queryset):
        count = retry_failures(queryset)
        self.message_user(
            request,
            f"Unparked {count} file(s); thumbnail generation queued.",
            messages.SUCCESS,
        )


@admin.register(FileScan)
class FileScanAdmin(ModelAdmin):
    """Malware scan verdicts, one row per scanned file.

    Every verdict is listed, ``clean`` included: being able to answer "was
    this file ever scanned, and when?" is the point of an audit surface, and
    the status filter narrows it to the interesting rows in one click.
    """

    list_display = ("file", "owner", "status", "signature", "scanned_at")
    list_filter = ("status", "scanned_at")
    list_select_related = ("file", "file__owner")
    search_fields = ("file__name", "signature")
    ordering = ("-scanned_at",)

    # Rows are written by the scan worker; there is nothing to author by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Owner", ordering="file__owner__username")
    def owner(self, obj):
        return obj.file.owner


class _QuotaAdminMixin:
    """Renders the two quota columns for both ModelAdmins and both inlines.

    ``bucket_pk`` names the attribute holding the bucket's owner; ``usage``
    reads the ``_usage`` annotation a changelist provides and falls back to
    the aggregate on a change form, where there is no queryset to annotate.
    """

    bucket_pk = None
    bucket_usage = None

    @admin.display(description="Limit")
    def limit(self, obj):
        return (
            "unlimited" if obj.quota_bytes is None else filesizeformat(obj.quota_bytes)
        )

    @admin.display(description="Current usage")
    def usage(self, obj):
        pk = None if obj is None else getattr(obj, self.bucket_pk)
        if pk is None:
            return "-"
        used = getattr(obj, "_usage", None)
        if used is None:
            used = self.bucket_usage(pk)
        if obj.quota_bytes is None:
            return f"{filesizeformat(used)} used (unlimited)"
        return f"{filesizeformat(used)} of {filesizeformat(obj.quota_bytes)}"


@admin.register(UserStorageQuota)
class UserStorageQuotaAdmin(_QuotaAdminMixin, ModelAdmin):
    """Every user whose personal limit deviates from STORAGE_QUOTA_BYTES."""

    list_display = ("user", "limit", "usage", "updated_at")
    list_select_related = ("user",)
    search_fields = ("user__username", "user__email", "note")
    autocomplete_fields = ("user",)
    readonly_fields = ("uuid", "usage", "updated_at")
    bucket_pk = "user_id"
    bucket_usage = staticmethod(personal_usage)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_usage=personal_usage_subquery())


@admin.register(GroupStorageQuota)
class GroupStorageQuotaAdmin(_QuotaAdminMixin, ModelAdmin):
    """Every group folder with a limit. A group with no row is unlimited."""

    list_display = ("group", "limit", "usage", "updated_at")
    list_select_related = ("group",)
    search_fields = ("group__name", "note")
    autocomplete_fields = ("group",)
    readonly_fields = ("uuid", "usage", "updated_at")
    bucket_pk = "group_id"
    bucket_usage = staticmethod(group_usage)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_usage=group_usage_subquery())


class UserStorageQuotaInline(_QuotaAdminMixin, StackedInline):
    """Set the personal quota from the user's own page."""

    model = UserStorageQuota
    extra = 0
    can_delete = True
    fields = ("quota_bytes", "usage", "note")
    readonly_fields = ("usage",)
    bucket_pk = "user_id"
    bucket_usage = staticmethod(personal_usage)


class GroupStorageQuotaInline(_QuotaAdminMixin, StackedInline):
    """Set the group folder's quota from the group's own page."""

    model = GroupStorageQuota
    extra = 0
    can_delete = True
    fields = ("quota_bytes", "usage", "note")
    readonly_fields = ("usage",)
    bucket_pk = "group_id"
    bucket_usage = staticmethod(group_usage)

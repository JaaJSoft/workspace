from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.decorators import display

from .models import ImportConnection, ImportJob, ImportJobItem


@admin.register(ImportConnection)
class ImportConnectionAdmin(ModelAdmin):
    list_display = ("label", "provider", "owner", "last_checked_at", "created_at")
    list_filter = ("provider",)
    list_select_related = ("owner",)
    search_fields = ("label", "owner__username", "base_url")
    autocomplete_fields = ("owner",)
    readonly_fields = (
        "uuid",
        "capabilities",
        "last_checked_at",
        "last_error",
        "created_at",
        "updated_at",
    )


@admin.register(ImportJob)
class ImportJobAdmin(ModelAdmin):
    list_display = (
        "uuid",
        "connection",
        "status_badge",
        "kinds",
        "created_at",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", ("created_at", RangeDateTimeFilter))
    list_filter_submit = True
    list_select_related = ("connection",)
    search_fields = ("uuid", "connection__label", "connection__owner__username")
    date_hierarchy = "created_at"

    @display(
        description="Status",
        label={
            ImportJob.Status.PENDING: "info",
            ImportJob.Status.RUNNING: "warning",
            ImportJob.Status.COMPLETED: "success",
            ImportJob.Status.FAILED: "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status

    # Jobs are created and driven by the import worker; a hand-made or edited
    # row would bypass the one-active-job-per-connection claim. Deletion stays
    # open for cleanup.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ImportJobItem)
class ImportJobItemAdmin(ModelAdmin):
    list_display = ("job", "kind", "remote_id", "status_badge", "created_at")
    list_filter = ("status", "kind")
    list_select_related = ("job",)
    search_fields = ("remote_id", "error", "job__uuid")

    @display(
        description="Status",
        label={
            ImportJobItem.Status.DONE: "success",
            ImportJobItem.Status.SKIPPED: "info",
            ImportJobItem.Status.FAILED: "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

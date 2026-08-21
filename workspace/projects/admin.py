from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import (
    Label,
    Project,
    ProjectMember,
    Subtask,
    Task,
    TaskComment,
    TaskEvent,
    TaskStatus,
)


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ("name", "key", "type", "created_by", "archived_at", "created_at")
    list_filter = ("type",)
    list_select_related = ("created_by",)
    search_fields = ("name", "key")
    autocomplete_fields = ("created_by",)
    filter_horizontal = ("groups",)
    readonly_fields = ("uuid", "next_task_number", "created_at", "updated_at")


@admin.register(ProjectMember)
class ProjectMemberAdmin(ModelAdmin):
    list_display = ("project", "user", "role", "joined_at", "left_at")
    list_filter = ("role",)
    list_select_related = ("project", "user")
    search_fields = ("project__name", "user__username")
    autocomplete_fields = ("project", "user")


@admin.register(TaskStatus)
class TaskStatusAdmin(ModelAdmin):
    list_display = ("name", "project", "category", "position")
    list_filter = ("category",)
    list_select_related = ("project",)
    search_fields = ("name", "project__name")
    autocomplete_fields = ("project",)


@admin.register(Label)
class LabelAdmin(ModelAdmin):
    list_display = ("name", "project", "color")
    list_select_related = ("project",)
    search_fields = ("name", "project__name")
    autocomplete_fields = ("project",)


@admin.register(Task)
class TaskAdmin(ModelAdmin):
    list_display = (
        "reference",
        "title",
        "project",
        "status",
        "priority_badge",
        "due_date",
        "completed_at",
        "created_at",
    )
    list_filter = ("priority",)
    list_select_related = ("project", "status", "created_by")
    search_fields = ("uuid", "title", "project__key", "project__name")
    autocomplete_fields = ("project", "status", "created_by")
    filter_horizontal = ("assignees", "labels")
    readonly_fields = ("uuid", "number", "created_at", "updated_at")
    date_hierarchy = "created_at"

    @display(
        description="Priority",
        label={
            Task.Priority.MEDIUM: "info",
            Task.Priority.HIGH: "warning",
            Task.Priority.URGENT: "danger",
        },
    )
    def priority_badge(self, obj):
        return obj.priority


@admin.register(Subtask)
class SubtaskAdmin(ModelAdmin):
    list_display = ("title", "task", "done", "position", "created_at")
    list_filter = ("done",)
    list_select_related = ("task",)
    search_fields = ("title", "task__title")
    autocomplete_fields = ("task",)
    readonly_fields = ("uuid", "created_at")


@admin.register(TaskComment)
class TaskCommentAdmin(ModelAdmin):
    list_display = ("task", "author", "created_at", "edited_at", "deleted_at")
    list_select_related = ("task", "author")
    search_fields = ("body", "author__username", "task__title")
    autocomplete_fields = ("task", "author")


@admin.register(TaskEvent)
class TaskEventAdmin(ModelAdmin):
    list_display = ("project", "task_title", "type", "actor", "created_at")
    list_filter = ("type",)
    list_select_related = ("project", "actor")
    search_fields = ("project__name", "task_title", "actor__username")
    date_hierarchy = "created_at"

    # Audit trail written by the task services; hand-edited rows would forge
    # history. Deletion stays open for retention cleanups.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

from django.conf import settings
from django.db import models
from django.db.models import Q

from workspace.common.uuids import uuid_v7_or_v4


class Project(models.Model):
    class Type(models.TextChoices):
        PERSONAL = "personal", "Personal"
        KANBAN = "kanban", "Kanban"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.KANBAN)
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_projects",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # One personal project per user; also the race-safety net for
            # get_or_create_personal_project.
            models.UniqueConstraint(
                fields=["created_by"],
                condition=Q(type="personal"),
                name="unique_personal_project_per_user",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_archived(self):
        return self.archived_at is not None


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    role = models.CharField(max_length=6, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                name="unique_project_member",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "left_at"]),
        ]

    def __str__(self):
        return f"{self.user} in {self.project} ({self.role})"


class TaskStatus(models.Model):
    class Category(models.TextChoices):
        BACKLOG = "backlog", "Backlog"
        ACTIVE = "active", "Active"
        DONE = "done", "Done"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="statuses",
    )
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=8, choices=Category.choices)
    color = models.CharField(max_length=20, blank=True, default="")
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="unique_status_name_per_project",
            ),
        ]

    def __str__(self):
        return f"{self.project}: {self.name}"


class Label(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="labels",
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="unique_label_name_per_project",
            ),
        ]

    def __str__(self):
        return f"{self.project}: {self.name}"


class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.ForeignKey(
        TaskStatus,
        on_delete=models.RESTRICT,
        related_name="tasks",
    )
    priority = models.CharField(
        max_length=6, choices=Priority.choices, default=Priority.MEDIUM
    )
    due_date = models.DateField(null=True, blank=True)
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="assigned_tasks",
    )
    labels = models.ManyToManyField(
        Label,
        blank=True,
        related_name="tasks",
    )
    position = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(
                fields=["project", "status", "position"],
                name="task_project_status_pos",
            ),
        ]

    def __str__(self):
        return self.title

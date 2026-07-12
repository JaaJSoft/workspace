from django.db import IntegrityError, transaction

from ..models import Project, ProjectMember, TaskStatus

DEFAULT_STATUSES = [
    ("Backlog", TaskStatus.Category.BACKLOG),
    ("To do", TaskStatus.Category.ACTIVE),
    ("In progress", TaskStatus.Category.ACTIVE),
    ("Done", TaskStatus.Category.DONE),
]


def create_project(
    user, *, name, description="", group=None, project_type=Project.Type.KANBAN
):
    """Create a project with its default statuses and the creator as admin."""
    with transaction.atomic():
        project = Project.objects.create(
            name=name,
            description=description,
            group=group,
            type=project_type,
            created_by=user,
        )
        TaskStatus.objects.bulk_create(
            TaskStatus(project=project, name=n, category=c, position=i)
            for i, (n, c) in enumerate(DEFAULT_STATUSES)
        )
        ProjectMember.objects.create(
            project=project, user=user, role=ProjectMember.Role.ADMIN
        )
    return project


def get_or_create_personal_project(user):
    """Return the user's personal project, creating it on first access.

    Race-safe through the partial unique constraint on (created_by) where
    type='personal': a concurrent create loses with IntegrityError and we
    re-read the winner's row.
    """
    project = Project.objects.filter(
        created_by=user, type=Project.Type.PERSONAL
    ).first()
    if project is not None:
        return project
    try:
        return create_project(user, name="Personal", project_type=Project.Type.PERSONAL)
    except IntegrityError:
        return Project.objects.get(created_by=user, type=Project.Type.PERSONAL)

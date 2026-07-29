from django.db import IntegrityError, transaction

from ..models import Project, ProjectMember, TaskStatus
from .references import unique_project_key

DEFAULT_STATUSES = [
    ("Backlog", TaskStatus.Category.BACKLOG),
    ("To do", TaskStatus.Category.ACTIVE),
    ("In progress", TaskStatus.Category.ACTIVE),
    ("Done", TaskStatus.Category.DONE),
]


def create_project(
    user, *, name, description="", groups=None, project_type=Project.Type.KANBAN
):
    """Create a project with its key, default statuses and creator as admin."""
    with transaction.atomic():
        project = _create_project_row(
            user,
            name=name,
            description=description,
            project_type=project_type,
        )
        if groups:
            project.groups.set(groups)
        TaskStatus.objects.bulk_create(
            TaskStatus(project=project, name=n, category=c, position=i)
            for i, (n, c) in enumerate(DEFAULT_STATUSES)
        )
        ProjectMember.objects.create(
            project=project, user=user, role=ProjectMember.Role.ADMIN
        )
    return project


def _create_project_row(user, *, name, description, project_type):
    """Insert the project row with a free key, retrying on key collisions.

    The unique constraint arbitrates concurrent creates: a loser regenerates
    against an updated taken set behind a savepoint. The last attempt runs
    without a net so a persistent IntegrityError (e.g. the personal-project
    constraint) propagates to get_or_create_personal_project unchanged.
    """
    taken = set(Project.objects.values_list("key", flat=True))
    taken.discard(None)
    for _ in range(2):
        key = unique_project_key(name, taken=taken)
        try:
            with transaction.atomic():
                return Project.objects.create(
                    name=name,
                    description=description,
                    type=project_type,
                    created_by=user,
                    key=key,
                )
        except IntegrityError:
            taken.add(key)
    return Project.objects.create(
        name=name,
        description=description,
        type=project_type,
        created_by=user,
        key=unique_project_key(name, taken=taken),
    )


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

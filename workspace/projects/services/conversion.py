from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Project, Sprint, TaskStatus
from .members import ProjectRuleError
from .sprints import assign_tasks_to_sprint


class ProjectTypeError(ProjectRuleError):
    """The requested project type change is not one the model allows."""


def convert_project_type(project, new_type, *, actor=None):
    """Switch a project between the kanban and scrum board models.

    Personal projects are excluded on both sides: their type encodes the
    one-per-user constraint, not a board layout. Converting to the type the
    project already carries is a no-op.
    """
    if new_type == project.type:
        return project
    if Project.Type.PERSONAL in (new_type, project.type):
        raise ProjectTypeError("Personal projects cannot change type.")
    if new_type not in (Project.Type.KANBAN, Project.Type.SCRUM):
        raise ProjectTypeError("Unknown project type.")
    with transaction.atomic():
        # Locking every sprint row serializes a conversion against a
        # concurrent start/complete, the same way complete_sprint does.
        sprints = list(project.sprints.select_for_update())
        if new_type == Project.Type.SCRUM:
            _open_first_sprint(project, sprints, actor=actor)
        else:
            _wind_down_sprints(sprints)
        project.type = new_type
        project.save(update_fields=["type", "updated_at"])
    return project


def _open_first_sprint(project, sprints, *, actor=None):
    """Put everything already on the board into a running sprint.

    The scrum board only renders the selected sprint, so tasks left without
    one would disappear from the columns they are standing in. Backlog
    columns are not part of the board and keep their tasks in the unplanned
    pool, ready for the first planning session. Tasks a closed sprint
    already completed keep it - that is the record of which sprint did the
    work - while its unfinished ones rejoin the new sprint.
    """
    sprint = next((s for s in sprints if s.state == Sprint.State.ACTIVE), None)
    if sprint is None:
        sprint = Sprint.objects.create(
            project=project,
            name=_free_sprint_name(sprints),
            state=Sprint.State.ACTIVE,
            start_date=timezone.localdate(),
        )
    unplanned = Q(sprint__isnull=True) | Q(sprint__state=Sprint.State.CLOSED)
    completed_in_a_sprint = Q(
        sprint__isnull=False, status__category=TaskStatus.Category.DONE
    )
    on_board = list(
        project.tasks.filter(unplanned)
        .exclude(completed_in_a_sprint)
        .exclude(status__category=TaskStatus.Category.BACKLOG)
        .values_list("uuid", flat=True)
    )
    if on_board:
        assign_tasks_to_sprint(project, sprint, on_board, actor=actor)
    return sprint


def _free_sprint_name(sprints):
    """The first free "Sprint N" name, so a past scrum life never collides."""
    taken = {sprint.name for sprint in sprints}
    n = 1
    while f"Sprint {n}" in taken:
        n += 1
    return f"Sprint {n}"


def _wind_down_sprints(sprints):
    """Retire the sprints a kanban board has no way to run.

    The running sprint becomes history - its tasks stay in their columns,
    which the kanban board shows in full, so nothing has to move. Planned
    sprints never ran and leave nothing worth recording, so they go; their
    tasks fall back to the unplanned pool through SET_NULL.
    """
    for sprint in sprints:
        if sprint.state != Sprint.State.ACTIVE:
            continue
        sprint.state = Sprint.State.CLOSED
        if sprint.end_date is None:
            sprint.end_date = timezone.localdate()
        sprint.save(update_fields=["state", "end_date"])
    planned = [s.pk for s in sprints if s.state == Sprint.State.PLANNED]
    if planned:
        Sprint.objects.filter(pk__in=planned).delete()

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import Task, TaskStatus
from .events import move_event_type, record_task_event
from .members import ProjectRuleError


class LastCategoryStatusError(ProjectRuleError):
    """A project must keep at least one column per category."""


class StatusTargetError(ProjectRuleError):
    """The reassignment target for a column deletion is missing or invalid."""


def create_status(project, *, name, category, color=""):
    """Create a column at the end of the project's list.

    Duplicate names surface as IntegrityError (unique_status_name_per_project);
    the caller maps it to a 400 field error, labels precedent.
    """
    last = project.statuses.aggregate(last=Max("position"))["last"]
    return TaskStatus.objects.create(
        project=project,
        name=name,
        category=category,
        color=color,
        position=0 if last is None else last + 1,
    )


def reorder_statuses(project, ordered_uuids):
    """Apply a manual order to the project's columns.

    Same contract as reorder_tasks: listed columns first in payload order,
    unlisted ones keep their previous relative order after the listed ones,
    unknown UUIDs are skipped. Idempotent.
    """
    with transaction.atomic():
        statuses = list(project.statuses.select_for_update())
        current = sorted(statuses, key=lambda s: (s.position, s.created_at))
        by_uuid = {s.uuid: s for s in statuses}

        sequence = []
        seen = set()
        for u in ordered_uuids:
            status = by_uuid.get(u)
            if status is not None and u not in seen:
                sequence.append(status)
                seen.add(u)
        for status in current:
            if status.uuid not in seen:
                sequence.append(status)
                seen.add(status.uuid)

        to_update = []
        for i, status in enumerate(sequence):
            if status.position != i:
                status.position = i
                to_update.append(status)
        if to_update:
            TaskStatus.objects.bulk_update(to_update, ["position"])


def delete_status(status, *, move_to=None, actor=None):
    """Delete a column, reassigning its remaining tasks to *move_to*.

    Guards:
    - never delete the last column of a category (the board, the backlog
      view and create_task all rely on every category being represented);
    - *move_to* is required while tasks remain, must belong to the same
      project and differ from the deleted column.

    Moved tasks land at the end of the target column, completed_at follows
    the target category, and one MOVED/COMPLETED event is written per task
    with the deleted column's name snapshotted while it still exists.
    """
    project = status.project
    with transaction.atomic():
        # Locking every column row serializes two concurrent deletions the
        # same way _other_active_admins_locked does for admin removals.
        statuses = list(project.statuses.select_for_update())
        has_category_sibling = any(
            s.category == status.category and s.pk != status.pk for s in statuses
        )
        if not has_category_sibling:
            raise LastCategoryStatusError(
                f"Cannot delete the last {status.category} column."
            )

        tasks = list(
            status.tasks.select_for_update().order_by("position", "created_at")
        )
        if tasks:
            if move_to is None:
                raise StatusTargetError("Target column required while tasks remain.")
            if move_to.pk == status.pk or move_to.project_id != project.pk:
                raise StatusTargetError(
                    "Target column must be a different column of this project."
                )
            last = project.tasks.filter(status=move_to).aggregate(last=Max("position"))[
                "last"
            ]
            next_position = 0 if last is None else last + 1
            now = timezone.now()
            for i, task in enumerate(tasks):
                task.status = move_to
                task.position = next_position + i
                task.project = project
                if move_to.category == TaskStatus.Category.DONE:
                    if task.completed_at is None:
                        task.completed_at = now
                else:
                    task.completed_at = None
                # bulk_update bypasses save(), so auto_now would leave
                # updated_at stale; stamp it by hand.
                task.updated_at = now
            Task.objects.bulk_update(
                tasks, ["status", "position", "completed_at", "updated_at"]
            )
            for task in tasks:
                record_task_event(
                    task,
                    type=move_event_type(move_to),
                    actor=actor,
                    from_status=status,
                    to_status=move_to,
                )
        status.delete()

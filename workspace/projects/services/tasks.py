from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from ..models import Task, TaskEvent, TaskStatus
from .events import move_event_type, record_task_event


def next_position(project, status):
    """Next free position at the end of *status*'s column."""
    last = project.tasks.filter(status=status).aggregate(last=Max("position"))["last"]
    return 0 if last is None else last + 1


def create_task(
    project,
    user,
    *,
    title,
    description="",
    status=None,
    priority=Task.Priority.MEDIUM,
    due_date=None,
    assignees=(),
    labels=(),
):
    """Create a task; defaults to the end of the project's backlog column."""
    if status is None:
        status = (
            project.statuses.filter(category=TaskStatus.Category.BACKLOG)
            .order_by("position", "created_at")
            .first()
        ) or project.statuses.order_by("position", "created_at").first()
    with transaction.atomic():
        task = Task.objects.create(
            project=project,
            title=title,
            description=description,
            status=status,
            priority=priority,
            due_date=due_date,
            created_by=user,
            position=next_position(project, status),
        )
        if assignees:
            task.assignees.set(assignees)
        if labels:
            task.labels.set(labels)
        if status.category == TaskStatus.Category.DONE:
            task.completed_at = timezone.now()
            task.save(update_fields=["completed_at"])
        record_task_event(
            task, type=TaskEvent.Type.CREATED, actor=user, to_status=status
        )
    return task


def apply_status_change(task, *, actor=None, old_status=None):
    """Side effects after ``task.status`` was reassigned.

    Appends the task to the end of its new column, maintains
    ``completed_at`` from the status category and records the move event.
    Saves the task.
    """
    task.position = next_position(task.project, task.status)
    if task.status.category == TaskStatus.Category.DONE:
        if task.completed_at is None:
            task.completed_at = timezone.now()
    else:
        task.completed_at = None
    with transaction.atomic():
        task.save(update_fields=["status", "position", "completed_at", "updated_at"])
        record_task_event(
            task,
            type=move_event_type(task.status),
            actor=actor,
            from_status=old_status,
            to_status=task.status,
        )


def delete_task(task, actor=None):
    """Delete *task*, leaving a DELETED event whose title snapshot survives.

    The event is written first: the task FK on the event is then nulled by
    the delete (SET_NULL), which is exactly the wanted end state.
    """
    with transaction.atomic():
        record_task_event(task, type=TaskEvent.Type.DELETED, actor=actor)
        task.delete()


def reorder_tasks(project, status, ordered_uuids, *, actor=None):
    """Apply a manual order to *status*'s column.

    Listed tasks from other statuses move into *status* (kanban cross-column
    drop); tasks of the column that the caller did not mention keep their
    previous relative order after the listed ones (pinned-folders precedent:
    handles concurrent creates/deletes gracefully). Unknown UUIDs are
    skipped. Idempotent: replaying the same payload yields the same state.
    """
    with transaction.atomic():
        # One locking query for both the column and the listed tasks: two
        # separate SELECT FOR UPDATE passes would leave a window between
        # them where a concurrent reorder locks the other half first.
        tasks = list(
            project.tasks.select_for_update().filter(
                Q(status=status) | Q(uuid__in=ordered_uuids)
            )
        )
        in_status = sorted(
            (t for t in tasks if t.status_id == status.pk),
            key=lambda t: (t.position, t.created_at),
        )
        by_uuid = {t.uuid: t for t in tasks}

        sequence = []
        seen = set()
        for u in ordered_uuids:
            task = by_uuid.get(u)
            if task is not None and u not in seen:
                sequence.append(task)
                seen.add(u)
        for t in in_status:
            if t.uuid not in seen:
                sequence.append(t)
                seen.add(t.uuid)

        now = timezone.now()
        to_update = []
        moved = []
        for i, task in enumerate(sequence):
            changed = False
            if task.status_id != status.pk:
                moved.append((task, task.status))
                task.status = status
                if status.category == TaskStatus.Category.DONE:
                    if task.completed_at is None:
                        task.completed_at = now
                elif task.completed_at is not None:
                    task.completed_at = None
                changed = True
            if task.position != i:
                task.position = i
                changed = True
            if changed:
                # bulk_update bypasses save(), so auto_now would leave
                # updated_at stale; stamp it by hand.
                task.updated_at = now
                to_update.append(task)
        if to_update:
            Task.objects.bulk_update(
                to_update, ["status", "position", "completed_at", "updated_at"]
            )
        for task, old_status in moved:
            record_task_event(
                task,
                type=move_event_type(status),
                actor=actor,
                from_status=old_status,
                to_status=status,
            )

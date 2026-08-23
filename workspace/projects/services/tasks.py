from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from workspace.notifications.services.notifications import settle_sources

from ..models import Task, TaskEvent, TaskStatus
from .assignments import notify_assigned
from .events import move_event_type, record_task_event
from .references import allocate_task_number


def settle_task_notifications(tasks):
    """Settle the due reminders of resolved tasks for every recipient.

    Called when tasks complete (or their due date moves back): the reminder
    is moot, whoever it was for. Capped at "normal" so high/urgent rows
    (comment mentions) stay unread - resolving a task does not prove its
    mentions were seen.
    """
    settle_sources(tasks, max_priority="normal")


def next_position(project, status):
    """Next free position at the end of *status*'s column."""
    last = project.tasks.filter(status=status).aggregate(last=Max("position"))["last"]
    return 0 if last is None else last + 1


def _locked_tail_position(project, status):
    """next_position with the *status* row locked for the transaction.

    The tail is read with a plain aggregate, so two concurrent writers
    appending to the same column would otherwise read the same
    Max(position) and write duplicate positions. Locking the status row
    first serializes every append path on the column. Must be called
    inside a transaction, before any task-row locks (all writers take
    the status lock first, keeping the lock order deadlock-free).
    """
    TaskStatus.objects.select_for_update().get(pk=status.pk)
    return next_position(project, status)


def create_task(
    project,
    user,
    *,
    title,
    description="",
    status=None,
    priority=Task.Priority.MEDIUM,
    due_date=None,
    estimate=None,
    assignees=(),
    labels=(),
    epic=None,
):
    """Create a task; defaults to the end of the project's backlog column."""
    # The API serializer scopes the epic per project; this guards the
    # direct callers (seeds, future tools) against cross-project grouping.
    if epic is not None and epic.project_id != project.pk:
        raise ValueError("Epic belongs to another project.")
    if status is None:
        status = (
            project.statuses.filter(category=TaskStatus.Category.BACKLOG)
            .order_by("position", "created_at")
            .first()
        ) or project.statuses.order_by("position", "created_at").first()
    with transaction.atomic():
        number = allocate_task_number(project)
        task = Task.objects.create(
            project=project,
            number=number,
            title=title,
            description=description,
            status=status,
            priority=priority,
            due_date=due_date,
            estimate=estimate,
            epic=epic,
            created_by=user,
            position=_locked_tail_position(project, status),
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
    if assignees:
        notify_assigned(task, user, assignees)
    return task


_UPDATE_EVENT_FIELDS = (
    "title",
    "description",
    "priority",
    "due_date",
    "assignees",
    "labels",
)


def has_field_updates(task, validated_data):
    """True when *validated_data* would change a non-status field of *task*.

    Status changes are excluded on purpose: they get their own MOVED or
    COMPLETED event via apply_status_change, and a no-op PATCH must not
    pollute the activity feed. Assignee additions are excluded for the same
    reason - they get their own ASSIGNED event, as are epic changes (EPIC)
    and estimate changes (ESTIMATED).
    """
    for field in _UPDATE_EVENT_FIELDS:
        if field not in validated_data:
            continue
        new = validated_data[field]
        if field == "assignees":
            # Additions get their own ASSIGNED event (see perform_update);
            # only removals count as a plain update.
            if {obj.pk for obj in task.assignees.all()} - {obj.pk for obj in new}:
                return True
        elif field == "labels":
            if {obj.pk for obj in new} != {obj.pk for obj in task.labels.all()}:
                return True
        elif getattr(task, field) != new:
            return True
    return False


def apply_status_change(task, *, actor=None, old_status=None):
    """Side effects after ``task.status`` was reassigned.

    Appends the task to the end of its new column, maintains
    ``completed_at`` from the status category and records the move event.
    Saves the task.
    """
    with transaction.atomic():
        task.position = _locked_tail_position(task.project, task.status)
        if task.status.category == TaskStatus.Category.DONE:
            if task.completed_at is None:
                task.completed_at = timezone.now()
        else:
            task.completed_at = None
        task.save(update_fields=["status", "position", "completed_at", "updated_at"])
        record_task_event(
            task,
            type=move_event_type(task.status),
            actor=actor,
            from_status=old_status,
            to_status=task.status,
        )
    if task.status.category == TaskStatus.Category.DONE:
        settle_task_notifications([task])


def delete_task(task, actor=None):
    """Delete *task*, leaving a DELETED event whose title snapshot survives.

    The event is written first: the task FK on the event is then nulled by
    the delete (SET_NULL), which is exactly the wanted end state.
    """
    with transaction.atomic():
        record_task_event(task, type=TaskEvent.Type.DELETED, actor=actor)
        task.delete()


def move_tasks(project, status, task_uuids, *, actor=None):
    """Move the listed tasks to the end of *status*, in their current order.

    Backlog bulk "send to board": unlike reorder_tasks this appends after
    the column's existing tasks instead of prepending. Tasks already in
    *status* and unknown UUIDs are skipped. Maintains ``completed_at`` from
    the status category and records one move event per moved task.
    Returns the moved tasks.
    """
    with transaction.atomic():
        position = _locked_tail_position(project, status)
        tasks = sorted(
            project.tasks.select_for_update().filter(uuid__in=task_uuids),
            key=lambda t: (t.position, t.created_at),
        )
        now = timezone.now()
        moved = []
        for task in tasks:
            if task.status_id == status.pk:
                continue
            moved.append((task, task.status))
            task.status = status
            task.position = position
            position += 1
            if status.category == TaskStatus.Category.DONE:
                if task.completed_at is None:
                    task.completed_at = now
            else:
                task.completed_at = None
            # bulk_update bypasses save(), so auto_now would leave
            # updated_at stale; stamp it by hand.
            task.updated_at = now
        if moved:
            Task.objects.bulk_update(
                [task for task, _ in moved],
                ["status", "position", "completed_at", "updated_at"],
            )
        for task, old_status in moved:
            record_task_event(
                task,
                type=move_event_type(status),
                actor=actor,
                from_status=old_status,
                to_status=status,
            )
    if moved and status.category == TaskStatus.Category.DONE:
        settle_task_notifications([task for task, _ in moved])
    return [task for task, _ in moved]


def reorder_tasks(project, status, ordered_uuids, *, actor=None):
    """Apply a manual order to *status*'s column.

    Listed tasks from other statuses move into *status* (kanban cross-column
    drop); tasks of the column that the caller did not mention keep their
    previous relative order after the listed ones (pinned-folders precedent:
    handles concurrent creates/deletes gracefully). Unknown UUIDs are
    skipped. Idempotent: replaying the same payload yields the same state.
    """
    with transaction.atomic():
        # Status row first (same lock order as _locked_tail_position): the
        # task-row locks below don't serialize writers when the column is
        # empty, since there are then no rows to lock.
        TaskStatus.objects.select_for_update().get(pk=status.pk)
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
    if moved and status.category == TaskStatus.Category.DONE:
        settle_task_notifications([task for task, _ in moved])

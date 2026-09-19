"""Bulk edits on a task selection: the backlog and all-tasks toolbars.

The single-task PATCH goes through the serializer and ``perform_update``;
a selection of a thousand tasks cannot afford one request each, so the
same field changes are applied here in one transaction, with the row
locks and the 1000-UUID cap of ``move_tasks``. The event trail stays the
one the PATCH leaves: an ASSIGNED event on every task that gained an
assignee, an UPDATED event on every task whose other fields changed - and
nothing on a task the operation left as it was.
"""

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from ..models import Task, TaskEvent
from .assignments import notify_bulk_assigned
from .events import record_task_event
from .tasks import delete_task, settle_task_notifications
from .watchers import auto_watch

# Distinguishes "leave due_date alone" from "clear it" (None is a value).
UNSET = object()


def _lock_tasks(project, task_uuids):
    """The project's tasks among *task_uuids*, row-locked in a fixed order.

    Ordered by primary key so two concurrent bulk edits over overlapping
    selections take their locks in the same sequence instead of
    deadlocking. ``of=("self",)`` keeps the lock off the joined project row.
    """
    return list(
        project.tasks.select_for_update(of=("self",))
        .select_related("project")
        .filter(uuid__in=task_uuids)
        .order_by("pk")
    )


def bulk_update_tasks(
    project,
    task_uuids,
    *,
    actor,
    assign=(),
    unassign=(),
    add_labels=(),
    remove_labels=(),
    priority=None,
    due_date=UNSET,
):
    """Apply the same field changes to every listed task of *project*.

    Set-oriented: an assignee or label the task already carries, a priority
    or due date it already has, is not a change - the task is left alone
    and gets no event. Unknown UUIDs and tasks of other projects are
    ignored. A due date earlier than a task's planned start is refused for
    that task alone (the single-task rule), and counted in ``skipped``.

    Returns ``(changed, skipped)``: the tasks that changed and the number
    left alone because of the start/due conflict. Assignment notifications
    collapse to one per recipient, whatever the number of tasks.
    """
    assign = list(assign)
    unassign = list(unassign)
    add_labels = list(add_labels)
    remove_labels = list(remove_labels)
    assignee_rows = Task.assignees.through
    label_rows = Task.labels.through
    with transaction.atomic():
        tasks = _lock_tasks(project, task_uuids)
        task_ids = [task.pk for task in tasks]
        current_assignees = defaultdict(set)
        for task_id, user_id in assignee_rows.objects.filter(
            task_id__in=task_ids
        ).values_list("task_id", "user_id"):
            current_assignees[task_id].add(user_id)
        current_labels = defaultdict(set)
        for task_id, label_id in label_rows.objects.filter(
            task_id__in=task_ids
        ).values_list("task_id", "label_id"):
            current_labels[task_id].add(label_id)

        now = timezone.now()
        added_assignees = {}  # task -> [User]
        plain_updates = []  # tasks earning an UPDATED event
        rows_to_write = []
        due_changed = []
        new_assignee_rows = []
        new_label_rows = []
        skipped = 0
        for task in tasks:
            if (
                due_date is not UNSET
                and due_date is not None
                and task.start_date is not None
                and task.start_date > due_date
            ):
                # Nothing else applies to it either: a half-applied edit
                # would leave the task looking updated while the one field
                # the user asked for was refused.
                skipped += 1
                continue
            changed = False
            plain = False
            added = [u for u in assign if u.pk not in current_assignees[task.pk]]
            if added:
                added_assignees[task] = added
                new_assignee_rows.extend(
                    assignee_rows(task_id=task.pk, user_id=u.pk) for u in added
                )
                changed = True
            if any(u.pk in current_assignees[task.pk] for u in unassign):
                changed = plain = True
            missing_labels = [
                lbl for lbl in add_labels if lbl.pk not in current_labels[task.pk]
            ]
            if missing_labels:
                new_label_rows.extend(
                    label_rows(task_id=task.pk, label_id=lbl.pk)
                    for lbl in missing_labels
                )
                changed = plain = True
            if any(lbl.pk in current_labels[task.pk] for lbl in remove_labels):
                changed = plain = True
            if priority is not None and task.priority != priority:
                task.priority = priority
                changed = plain = True
            if due_date is not UNSET and task.due_date != due_date:
                task.due_date = due_date
                due_changed.append(task)
                changed = plain = True
            if not changed:
                continue
            # bulk_update bypasses save(), so auto_now would leave
            # updated_at stale; stamp it by hand.
            task.updated_at = now
            rows_to_write.append(task)
            if plain:
                plain_updates.append(task)

        if new_assignee_rows:
            assignee_rows.objects.bulk_create(new_assignee_rows)
        if unassign:
            assignee_rows.objects.filter(
                task_id__in=[t.pk for t in rows_to_write],
                user_id__in=[u.pk for u in unassign],
            ).delete()
        if new_label_rows:
            label_rows.objects.bulk_create(new_label_rows)
        if remove_labels:
            label_rows.objects.filter(
                task_id__in=[t.pk for t in rows_to_write],
                label_id__in=[lbl.pk for lbl in remove_labels],
            ).delete()
        if rows_to_write:
            Task.objects.bulk_update(
                rows_to_write, ["priority", "due_date", "updated_at"]
            )
        for task, users in added_assignees.items():
            record_task_event(task, type=TaskEvent.Type.ASSIGNED, actor=actor)
            auto_watch(task, users)
        for task in plain_updates:
            record_task_event(task, type=TaskEvent.Type.UPDATED, actor=actor)

    # Same rule as the single-task PATCH: a reminder is moot once the
    # deadline is gone or pushed past today.
    settled = [
        task
        for task in due_changed
        if task.due_date is None or task.due_date > timezone.localdate()
    ]
    if settled:
        settle_task_notifications(settled)
    tasks_by_user = defaultdict(list)
    for task, users in added_assignees.items():
        for user in users:
            tasks_by_user[user].append(task)
    if tasks_by_user:
        notify_bulk_assigned(project, actor, tasks_by_user)
    return rows_to_write, skipped


def delete_tasks(project, task_uuids, *, actor=None):
    """Delete the listed tasks of *project*, one DELETED event each.

    Unknown UUIDs and tasks of other projects are skipped. Each task goes
    through ``delete_task`` so its file links are revoked or handed over
    rather than orphaned. Returns the number of deleted tasks.
    """
    with transaction.atomic():
        tasks = _lock_tasks(project, task_uuids)
        for task in tasks:
            delete_task(task, actor=actor)
    return len(tasks)

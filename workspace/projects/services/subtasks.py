from django.db import transaction
from django.db.models import Max

from ..models import Subtask, Task


def create_subtask(task, title):
    """Append a checklist item at the end of *task*'s checklist.

    The task row is locked first: two concurrent adds would otherwise read
    the same Max(position) and write duplicate positions.
    """
    with transaction.atomic():
        Task.objects.select_for_update().get(pk=task.pk)
        last = task.subtasks.aggregate(last=Max("position"))["last"]
        return Subtask.objects.create(
            task=task,
            title=title,
            position=0 if last is None else last + 1,
        )


def reorder_subtasks(task, ordered_uuids):
    """Apply a manual order to *task*'s checklist.

    Same idempotent full-order contract as reorder_tasks: items the caller
    did not mention keep their previous relative order after the listed
    ones (handles concurrent adds/deletes gracefully), unknown UUIDs are
    skipped, and replaying the same payload yields the same state.
    """
    with transaction.atomic():
        # Task row first, mirroring create_subtask's lock order: the item
        # locks alone don't serialize writers when the checklist is empty.
        Task.objects.select_for_update().get(pk=task.pk)
        items = list(task.subtasks.select_for_update())
        current = sorted(items, key=lambda s: (s.position, s.created_at))
        by_uuid = {s.uuid: s for s in items}

        sequence = []
        seen = set()
        for u in ordered_uuids:
            item = by_uuid.get(u)
            if item is not None and u not in seen:
                sequence.append(item)
                seen.add(u)
        for item in current:
            if item.uuid not in seen:
                sequence.append(item)
                seen.add(item.uuid)

        to_update = []
        for i, item in enumerate(sequence):
            if item.position != i:
                item.position = i
                to_update.append(item)
        if to_update:
            Subtask.objects.bulk_update(to_update, ["position"])

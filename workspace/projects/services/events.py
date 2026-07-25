from ..models import TaskEvent, TaskStatus


def record_task_event(task, *, type, actor=None, from_status=None, to_status=None):
    """Insert one TaskEvent row, snapshotting the task title and status names."""
    return TaskEvent.objects.create(
        project=task.project,
        task=task,
        task_title=task.title,
        actor=actor,
        type=type,
        from_status=from_status.name if from_status is not None else "",
        to_status=to_status.name if to_status is not None else "",
    )


def move_event_type(to_status):
    """Event type for a move into *to_status*: landing on a Done column is a
    completion, anything else (including reopening) is a plain move."""
    if to_status.category == TaskStatus.Category.DONE:
        return TaskEvent.Type.COMPLETED
    return TaskEvent.Type.MOVED


def events_for_project(project, limit=15):
    """Newest-first events for the project overview card."""
    return project.task_events.select_related("actor")[:limit]

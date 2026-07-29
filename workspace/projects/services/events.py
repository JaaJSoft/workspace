from ..models import TaskEvent, TaskStatus


def record_task_event(task, *, type, actor=None, from_status=None, to_status=None):
    """Insert one TaskEvent row, snapshotting the task title and status names."""
    return TaskEvent.objects.create(
        project=task.project,
        task=task,
        task_title=task.title,
        task_number=task.number,
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
    return project.task_events.select_related("actor", "project")[:limit]


def serialize_task_event(ev):
    """Normalize a TaskEvent into the activity-feed event dict shape.

    Shared between the activity provider and the task detail panel so both
    render through dashboard/partials/activity_item.html.
    """
    if ev.actor is not None:
        actor = {
            "id": ev.actor.pk,
            "username": ev.actor.username,
            "full_name": ev.actor.get_full_name(),
        }
    else:
        # Null actor means a system-driven write; never attribute it to
        # a real user (same convention as the files provider).
        actor = None
    # Deep-link to the task panel while the task exists; a deleted task
    # (SET_NULL on the event) falls back to the project page.
    url = f"/projects/{ev.project_id}"
    if ev.task_id is not None:
        url = f"{url}?task={ev.task_id}"
    description = ev.task_title
    if ev.task_number is not None:
        description = f"{ev.project.key}-{ev.task_number} · {ev.task_title}"
    return {
        "icon": ev.icon,
        "label": ev.short_label,
        "description": description,
        "timestamp": ev.created_at,
        "url": url,
        "actor": actor,
    }

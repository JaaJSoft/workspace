from ..models import TaskEvent, TaskStatus


def record_task_event(
    task,
    *,
    type,
    actor=None,
    from_status=None,
    to_status=None,
    from_value="",
    to_value="",
    from_ref=None,
    to_ref=None,
):
    """Insert one TaskEvent row, snapshotting the task title and the names
    and categories of the statuses involved. *from_ref*/*to_ref* carry the
    identity behind a name in *from_value*/*to_value* (a sprint's UUID)."""
    return TaskEvent.objects.create(
        project=task.project,
        task=task,
        task_title=task.title,
        task_number=task.number,
        actor=actor,
        type=type,
        from_status=from_status.name if from_status is not None else "",
        to_status=to_status.name if to_status is not None else "",
        from_category=from_status.category if from_status is not None else "",
        to_category=to_status.category if to_status is not None else "",
        from_value=from_value,
        to_value=to_value,
        from_ref=from_ref,
        to_ref=to_ref,
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
    render through core/partials/activity_item.html.
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
    label = ev.short_label
    if ev.type == TaskEvent.Type.ESTIMATED:
        if ev.from_value and ev.to_value:
            label = f"Estimate changed: {ev.from_value} → {ev.to_value}"
        elif ev.to_value:
            label = f"Estimate set to {ev.to_value}"
        elif ev.from_value:
            label = "Estimate removed"
    elif ev.type == TaskEvent.Type.EPIC:
        # from_value/to_value carry the epic names snapshotted at change time.
        if ev.from_value and ev.to_value:
            label = f"Epic changed: {ev.from_value} → {ev.to_value}"
        elif ev.to_value:
            label = f"Epic set to {ev.to_value}"
        elif ev.from_value:
            label = "Epic removed"
    elif ev.type == TaskEvent.Type.SPRINT:
        # from_value/to_value carry the sprint names snapshotted at change
        # time, same rationale as the epic names.
        if ev.from_value and ev.to_value:
            label = f"Sprint changed: {ev.from_value} → {ev.to_value}"
        elif ev.to_value:
            label = f"Added to sprint {ev.to_value}"
        elif ev.from_value:
            label = f"Removed from sprint {ev.from_value}"
    elif ev.type in (TaskEvent.Type.LINKED, TaskEvent.Type.UNLINKED):
        # from_value holds the direction label ("blocks", "is blocked by"),
        # to_value the other end's reference, both snapshotted at link time.
        if ev.from_value and ev.to_value:
            label = f"{ev.short_label}: {ev.from_value} {ev.to_value}"
    return {
        "icon": ev.icon,
        "label": label,
        "description": description,
        "timestamp": ev.created_at,
        "url": url,
        "actor": actor,
    }

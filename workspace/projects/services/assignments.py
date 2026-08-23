"""Notification fan-out for task assignment."""

from workspace.notifications.services.notifications import notify_stream

from .notification_levels import apply_levels


def _task_url(task):
    return f"/projects/{task.project_id}/board?task={task.uuid}"


def notify_assigned(task, actor, assignees):
    """Notify *assignees* they were put on *task* - never the actor.

    Keyed on the task with its own stream: a rapid re-assignment merges into
    the still-unread row instead of stacking, and can never repurpose a
    mention or comment notification sharing the task.
    """
    recipients = [u for u in assignees if u.pk != actor.pk and u.is_active]
    recipients, priority_map = apply_levels(task.project_id, recipients)
    if not recipients:
        return
    notify_stream(
        recipient_ids=[u.pk for u in recipients],
        source=task,
        origin="projects",
        title=f'{actor.username} assigned you to "{task.title}"',
        url=_task_url(task),
        actor=actor,
        priority_map=priority_map,
        stream="assignment",
    )

"""Notification fan-out for task assignment."""

from workspace.notifications.services.notifications import notify, notify_stream

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


def notify_bulk_assigned(project, actor, tasks_by_user):
    """One notification per recipient for a bulk assignment - never the actor.

    *tasks_by_user* maps each user to the tasks they were just put on. A
    user put on a single task gets the ordinary per-task row, deep-linked
    to it; a user put on several gets one row carrying the count and
    linking to the project's task list filtered on them. Twenty rows for
    one gesture would bury everything else in the drawer.
    """
    for user, tasks in tasks_by_user.items():
        if user.pk == actor.pk or not user.is_active:
            continue
        if len(tasks) == 1:
            notify_assigned(tasks[0], actor, [user])
            continue
        recipients, priority_map = apply_levels(project.pk, [user])
        if not recipients:
            continue
        notify(
            recipient=user,
            origin="projects",
            title=f"{actor.username} assigned you to {len(tasks)} tasks in {project.name}",
            url=f"/projects/{project.uuid}/tasks?assignee={user.pk}",
            actor=actor,
            priority=priority_map.get(user.pk, "normal"),
        )

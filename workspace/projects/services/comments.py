"""Notification fan-out for task comments."""

from django.contrib.auth import get_user_model

from workspace.common.services.mentions import mentioned_users, newly_mentioned_users
from workspace.notifications.services.notifications import notify_stream

from ..queries import project_users

User = get_user_model()


def _task_url(task):
    return f"/projects/{task.project_id}/board?task={task.uuid}"


def _notify_mentioned(task, actor, mentioned):
    notify_stream(
        recipient_ids=[u.pk for u in mentioned],
        source=task,
        origin="projects",
        title=f'{actor.username} mentioned you in a comment on "{task.title}"',
        url=_task_url(task),
        actor=actor,
        default_priority="high",
    )


def notify_comment_added(task, actor, body):
    """Notify about a new comment (never the actor).

    Project members mentioned in *body* get a high-priority mention
    notification; the task creator, assignees, and prior commenters get the
    regular one.
    """
    mentioned = mentioned_users(project_users(task.project), body, actor)
    if mentioned:
        _notify_mentioned(task, actor, mentioned)
    mentioned_ids = {u.pk for u in mentioned}

    recipients = set(task.assignees.all())
    if task.created_by:
        recipients.add(task.created_by)
    commenter_ids = (
        task.comments.filter(deleted_at__isnull=True)
        .exclude(author=actor)
        .values_list("author", flat=True)
        .distinct()
    )
    recipients.update(User.objects.filter(pk__in=commenter_ids))
    recipients.discard(actor)
    recipients = [u for u in recipients if u.pk not in mentioned_ids]
    if recipients:
        notify_stream(
            recipient_ids=[u.pk for u in recipients],
            source=task,
            origin="projects",
            title=f'{actor.username} commented on "{task.title}"',
            url=_task_url(task),
            actor=actor,
        )


def notify_comment_edited(task, actor, old_body, new_body):
    """Notify only project members newly mentioned by the edit."""
    newly_mentioned = newly_mentioned_users(
        project_users(task.project), actor, old_body, new_body
    )
    if newly_mentioned:
        _notify_mentioned(task, actor, newly_mentioned)

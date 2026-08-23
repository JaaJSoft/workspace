"""Task comments: creation and notification fan-out."""

from django.contrib.auth import get_user_model

from workspace.common.services.mentions import mentioned_users, newly_mentioned_users
from workspace.notifications.services.notifications import notify_stream

from ..models import TaskComment, TaskEvent
from ..queries import project_users
from .events import record_task_event
from .notification_levels import apply_levels
from .watchers import apply_watchers, auto_watch

User = get_user_model()


def add_comment(task, author, body):
    """Create a comment with its event, auto-watch and notification fan-out.

    The single write path for comments: the API viewset and the AI tool
    both go through here so the COMMENTED event, the author's auto-watch
    and the recipient fan-out can never drift apart.
    """
    comment = TaskComment.objects.create(task=task, author=author, body=body)
    record_task_event(task, type=TaskEvent.Type.COMMENTED, actor=author)
    auto_watch(task, [author])
    notify_comment_added(task, author, comment.body)
    return comment


def _task_url(task):
    return f"/projects/{task.project_id}/board?task={task.uuid}"


def _notify_mentioned(task, actor, mentioned):
    recipients, priority_map = apply_levels(task.project_id, mentioned)
    if not recipients:
        return
    notify_stream(
        recipient_ids=[u.pk for u in recipients],
        source=task,
        origin="projects",
        title=f'{actor.username} mentioned you in a comment on "{task.title}"',
        url=_task_url(task),
        actor=actor,
        default_priority="high",
        priority_map=priority_map,
    )


def notify_comment_added(task, actor, body):
    """Notify about a new comment (never the actor).

    Project members mentioned in *body* get a high-priority mention
    notification - a mention is personal, so a muted watch does not block
    it. The regular one goes to the implicit set (task creator, assignees,
    prior commenters) plus the task's watchers, minus muted users and
    anyone who lost project access.
    """
    users = project_users(task.project)
    mentioned = mentioned_users(users, body, actor)
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
    allowed_by_id = {u.pk: u for u in users if u.is_active}
    recipients = apply_watchers(task, recipients, allowed_by_id)
    # After the watcher union: an actor or mentioned user watching the
    # task would otherwise be re-added to the regular set.
    recipients = [
        u for u in recipients if u.pk != actor.pk and u.pk not in mentioned_ids
    ]
    recipients, priority_map = apply_levels(task.project_id, recipients)
    if recipients:
        notify_stream(
            recipient_ids=[u.pk for u in recipients],
            source=task,
            origin="projects",
            title=f'{actor.username} commented on "{task.title}"',
            url=_task_url(task),
            actor=actor,
            priority_map=priority_map,
        )


def notify_comment_edited(task, actor, old_body, new_body):
    """Notify only project members newly mentioned by the edit."""
    newly_mentioned = newly_mentioned_users(
        project_users(task.project), actor, old_body, new_body
    )
    if newly_mentioned:
        _notify_mentioned(task, actor, newly_mentioned)

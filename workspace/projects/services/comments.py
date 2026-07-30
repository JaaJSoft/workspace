"""Notification fan-out for task comments."""

from django.contrib.auth import get_user_model

from workspace.notifications.services.notifications import notify_many

User = get_user_model()


def notify_comment_added(task, actor):
    """Notify the task creator, assignees, and prior commenters (never the actor)."""
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
    if recipients:
        notify_many(
            recipients=list(recipients),
            origin="projects",
            title=f'{actor.username} commented on "{task.title}"',
            url=f"/projects/{task.project_id}/board?task={task.uuid}",
            actor=actor,
        )

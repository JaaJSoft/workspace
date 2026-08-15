import logging
from collections import defaultdict

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="projects.notify_due_tasks", ignore_result=True)
def notify_due_tasks():
    """Notify assignees of tasks that are due today or overdue.

    Runs daily. Keyed per task through ``notify_stream``: a rerun merges into
    the existing unread notification instead of stacking a duplicate, so the
    schedule can be replayed safely. A notification the user already read is
    recreated on the next run while the task stays due - a daily reminder,
    not an all-day badge. Completing the task or pushing its due date back
    settles the notification (see ``apply_status_change`` and the task
    update view).
    """
    from workspace.notifications.services.notifications import notify_stream
    from workspace.projects.queries import due_open_tasks, project_users

    today = timezone.localdate()
    tasks_by_project = defaultdict(list)
    for task in due_open_tasks().prefetch_related("assignees"):
        tasks_by_project[task.project].append(task)

    notified = 0
    for project, tasks in tasks_by_project.items():
        # Assignees who left the project (or lost group access) keep their
        # assignee row; they must not keep receiving reminders.
        allowed_ids = {u.pk for u in project_users(project) if u.is_active}
        for task in tasks:
            recipient_ids = [u.pk for u in task.assignees.all() if u.pk in allowed_ids]
            if not recipient_ids:
                continue
            if task.due_date < today:
                body = f"Overdue since {task.due_date.isoformat()} · {project.name}"
            else:
                body = f"Due today · {project.name}"
            notify_stream(
                recipient_ids=recipient_ids,
                source=task,
                origin="projects",
                title=task.title,
                body=body,
                url=f"/projects/{task.project_id}?task={task.uuid}",
                stream="reminder",
            )
            notified += 1
    if notified:
        logger.info("Due-task notifications sent for %d tasks", notified)
    return notified

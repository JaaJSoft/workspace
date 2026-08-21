import logging
from collections import defaultdict

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="projects.notify_due_tasks", ignore_result=True)
def notify_due_tasks():
    """Send each assignee one reminder when a task falls due and one more
    when it becomes overdue - never a daily repeat.

    Runs hourly because "due today" is judged against each recipient's local
    date, and those dates roll over at different wall-clock hours across
    timezones. ``TaskReminder`` rows record what was already sent, so reruns
    skip them; a moved due date no longer matches its rows, which re-arms
    both reminders for the new date. Completing the task or pushing its due
    date back settles any still-unread notification (see
    ``apply_status_change`` and the task update view).
    """
    from workspace.notifications.services.notifications import notify_stream
    from workspace.projects.models import TaskReminder
    from workspace.projects.queries import due_open_tasks, project_users
    from workspace.users.services.settings import get_user_timezone

    now = timezone.now()
    tasks_by_project = defaultdict(list)
    for task in due_open_tasks().prefetch_related("assignees"):
        tasks_by_project[task.project].append(task)

    sent = 0
    local_dates = {}
    for project, tasks in tasks_by_project.items():
        # Assignees who left the project (or lost group access) keep their
        # assignee row; they must not keep receiving reminders.
        allowed_ids = {u.pk for u in project_users(project) if u.is_active}
        reminded = {
            (r.task_id, r.user_id, r.kind): r.due_date
            for r in TaskReminder.objects.filter(task__in=tasks)
        }
        for task in tasks:
            recipients_by_kind = defaultdict(list)
            for user in task.assignees.all():
                if user.pk not in allowed_ids:
                    continue
                if user.pk not in local_dates:
                    local_dates[user.pk] = now.astimezone(
                        get_user_timezone(user)
                    ).date()
                today = local_dates[user.pk]
                if task.due_date > today:
                    continue
                kind = (
                    TaskReminder.Kind.OVERDUE
                    if task.due_date < today
                    else TaskReminder.Kind.DUE
                )
                if reminded.get((task.uuid, user.pk, kind)) == task.due_date:
                    continue
                recipients_by_kind[kind].append(user.pk)
            for kind, recipient_ids in recipients_by_kind.items():
                if kind == TaskReminder.Kind.OVERDUE:
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
                for uid in recipient_ids:
                    TaskReminder.objects.update_or_create(
                        task=task,
                        user_id=uid,
                        kind=kind,
                        defaults={"due_date": task.due_date},
                    )
                sent += len(recipient_ids)
    if sent:
        logger.info("Sent %d due-task reminders", sent)
    return sent

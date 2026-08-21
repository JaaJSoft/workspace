import logging
from collections import defaultdict

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_REMINDER_HOUR = 8


def _reminder_hour(user):
    """Local hour (0-23) from which the user's due reminders may be sent.

    The setting is JSON from the API, so anything malformed falls back to
    the default rather than silencing the user's reminders forever.
    """
    from workspace.users.services.settings import get_setting

    raw = get_setting(user, "projects", "reminder_hour", default=DEFAULT_REMINDER_HOUR)
    try:
        hour = int(raw)
    except TypeError, ValueError:
        return DEFAULT_REMINDER_HOUR
    return hour if 0 <= hour <= 23 else DEFAULT_REMINDER_HOUR


@shared_task(name="projects.notify_due_tasks", ignore_result=True)
def notify_due_tasks():
    """Send each assignee one reminder when a task falls due and one more
    when it becomes overdue - never a daily repeat.

    Runs hourly because reminders follow each recipient's wall clock: a
    reminder goes out on the first run after the user's configured morning
    hour (``projects.reminder_hour``, default 8:00) of the relevant local
    day. ``TaskReminder`` rows record what was already sent, so reruns skip
    them; a moved due date no longer matches its rows, which re-arms both
    reminders for the new date. Completing the task or pushing its due date
    back settles any still-unread notification (see ``apply_status_change``
    and the task update view).
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
    local_times = {}
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
                if user.pk not in local_times:
                    local_times[user.pk] = (
                        now.astimezone(get_user_timezone(user)),
                        _reminder_hour(user),
                    )
                local_now, from_hour = local_times[user.pk]
                today = local_now.date()
                if task.due_date > today or local_now.hour < from_hour:
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

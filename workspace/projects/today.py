"""The projects' source for the dashboard's Today card."""

from django.db.models import BooleanField, Case, Q, Value, When
from django.utils import timezone

from workspace.core.today_registry import capped_rows
from workspace.projects.models import Task
from workspace.projects.queries import assigned_open_tasks

TASKS_LIMIT = 5


def tasks_for_today(user, now, tz):
    """The user's tasks to deal with today: overdue, due today or urgent.

    Urgent ones (overdue or of urgent priority) come first.

    Each row carries ``overdue`` and ``due_today``, read on the user's
    local day.
    """
    today = timezone.localdate(now, timezone=tz)
    urgent = Q(due_date__lt=today) | Q(priority=Task.Priority.URGENT)
    tasks = (
        assigned_open_tasks(user)
        .filter(urgent | Q(due_date=today))
        # The cap must not drop an urgent task with no due date, which the
        # due-date order would put last.
        .annotate(
            urgent=Case(
                When(urgent, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
    )
    tasks = tasks.order_by("-urgent", *tasks.query.order_by)
    rows = capped_rows(tasks, TASKS_LIMIT)
    for task in rows.items:
        task.overdue = task.due_date is not None and task.due_date < today
        task.due_today = task.due_date == today
    return rows


def is_urgent_task(task):
    return task.urgent

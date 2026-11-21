"""The projects' source for the dashboard's Today card."""

from django.db.models import Q
from django.utils import timezone

from workspace.core.today_registry import TodayRows, capped_rows
from workspace.projects.models import Task
from workspace.projects.queries import assigned_open_tasks

TASKS_LIMIT = 5


def tasks_for_today(user, now, tz):
    """The user's tasks to deal with today, or the next ones when none is.

    Today's tasks are those overdue, due today or marked urgent. When there
    are none, the next assigned tasks stand in so the card never loses the
    user's work entirely; their rows carry ``coming_up`` to say so, and
    they get no counter: none of them is due.
    """
    today = timezone.localdate(now, timezone=tz)
    tasks = assigned_open_tasks(user)
    rows = capped_rows(
        tasks.filter(Q(due_date__lte=today) | Q(priority=Task.Priority.URGENT)),
        TASKS_LIMIT,
    )
    coming_up = not rows.items
    if coming_up:
        rows = TodayRows(list(tasks[:TASKS_LIMIT]), None)
    for task in rows.items:
        task.coming_up = coming_up
        task.overdue = task.due_date is not None and task.due_date < today
        task.due_today = task.due_date == today
    return rows

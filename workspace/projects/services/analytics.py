from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncWeek
from django.utils import timezone

from ..models import Task, TaskEvent, TaskStatus

DEFAULT_WEEKS = 12

_PRIORITY_ORDER = [
    Task.Priority.URGENT,
    Task.Priority.HIGH,
    Task.Priority.MEDIUM,
    Task.Priority.LOW,
]
_PRIORITY_CSS = {
    Task.Priority.URGENT: "bg-error",
    Task.Priority.HIGH: "bg-warning",
    Task.Priority.MEDIUM: "bg-info",
    Task.Priority.LOW: "bg-neutral",
}


def weekly_flow(project, weeks=DEFAULT_WEEKS):
    """Created and completed event counts per ISO week, oldest first.

    Always returns exactly *weeks* buckets, the last being the current,
    partial week. Quiet weeks are filled with zeros: dropping them would
    compress the x-axis and misrepresent the trend.

    A task completed, reopened and completed again counts once per
    completion. Deduplicating with Count(task_id, distinct=True) would
    instead drop every deleted task, since TaskEvent.task is SET_NULL.
    """
    starts = _week_starts(weeks)
    counts = {start.date(): {"created": 0, "completed": 0} for start in starts}
    rows = (
        TaskEvent.objects.filter(
            project=project,
            type__in=(TaskEvent.Type.CREATED, TaskEvent.Type.COMPLETED),
            created_at__gte=starts[0],
        )
        .annotate(week=TruncWeek("created_at"))
        .values("week", "type")
        .annotate(n=Count("uuid"))
    )
    for row in rows:
        bucket = counts.get(_as_date(row["week"]))
        if bucket is not None:
            bucket[row["type"]] += row["n"]
    return [{"week_start": start.date(), **counts[start.date()]} for start in starts]


def flow_summary(rows):
    """Headline numbers for the flow chart. A positive net means the
    backlog grew over the window."""
    created = sum(row["created"] for row in rows)
    completed = sum(row["completed"] for row in rows)
    return {
        "created": created,
        "completed": completed,
        "net": created - completed,
        "avg_weekly": round(completed / len(rows), 1) if rows else 0,
    }


def open_task_distribution(project):
    """Where the project's open work currently sits."""
    open_tasks = project.tasks.exclude(status__category=TaskStatus.Category.DONE)
    return {
        "by_status": _by_status(project, open_tasks),
        "by_assignee": _by_assignee(open_tasks),
        "by_priority": _by_priority(open_tasks),
    }


def _week_starts(weeks):
    """Aware UTC midnights on the Monday of each bucket, oldest first.

    TIME_ZONE is UTC and TruncWeek truncates to Monday, so these line up
    with the values the database returns.
    """
    now = timezone.now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return [monday - timedelta(weeks=i) for i in reversed(range(weeks))]


def _as_date(value):
    """TruncWeek yields a datetime on PostgreSQL and may yield a date on
    SQLite; bucket keys are dates so both land in the same slot."""
    return value.date() if hasattr(value, "date") else value


def _by_status(project, open_tasks):
    counts = dict(
        open_tasks.values_list("status_id")
        .annotate(n=Count("uuid"))
        .values_list("status_id", "n")
    )
    statuses = project.statuses.exclude(category=TaskStatus.Category.DONE).order_by(
        "position", "created_at"
    )
    return [
        {
            "label": status.name,
            "count": counts.get(status.pk, 0),
            "color": status.color,
            "css_class": "bg-accent",
        }
        for status in statuses
    ]


def _by_assignee(open_tasks):
    """Busiest first. A task with several assignees counts on every plate:
    this answers "how loaded is each person", not "how is work split"."""
    entries = [
        {
            "label": row["assignees__username"],
            "count": row["n"],
            "color": "",
            "css_class": "bg-accent",
        }
        for row in open_tasks.filter(assignees__isnull=False)
        .values("assignees__username")
        .annotate(n=Count("uuid"))
        .order_by("-n", "assignees__username")
    ]
    # Counted separately: the M2M join drops tasks with no assignee, so
    # without this bucket the chart's total disagrees with the others.
    unassigned = open_tasks.filter(assignees__isnull=True).count()
    if unassigned:
        entries.append(
            {
                "label": "Unassigned",
                "count": unassigned,
                "color": "",
                "css_class": "bg-base-content/30",
            }
        )
    return entries


def _by_priority(open_tasks):
    counts = dict(
        open_tasks.values_list("priority")
        .annotate(n=Count("uuid"))
        .values_list("priority", "n")
    )
    return [
        {
            "label": Task.Priority(priority).label,
            "count": counts.get(priority, 0),
            "color": "",
            "css_class": _PRIORITY_CSS[priority],
        }
        for priority in _PRIORITY_ORDER
    ]

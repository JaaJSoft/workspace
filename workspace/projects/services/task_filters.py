"""Task filtering and ordering from query parameters.

Shared by the REST task list endpoint and the board/backlog/all-tasks UI
views so both surfaces accept the same URL-shareable filter state.
"""

from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils.dateparse import parse_date

from workspace.common.booleans import is_truthy
from workspace.common.uuids import parse_uuid_or_none

from ..models import Task, TaskStatus
from .search import fts_tasks


class TaskFilterError(Exception):
    """A malformed filter or ordering parameter; callers map it to a 400."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


# Rank so that ascending `ordering=priority` puts the most important first.
PRIORITY_RANK = Case(
    When(priority=Task.Priority.URGENT, then=Value(0)),
    When(priority=Task.Priority.HIGH, then=Value(1)),
    When(priority=Task.Priority.MEDIUM, then=Value(2)),
    When(priority=Task.Priority.LOW, then=Value(3)),
    output_field=IntegerField(),
)

ORDERABLE_FIELDS = frozenset(
    {"position", "priority", "due_date", "estimate", "created_at", "updated_at"}
)

TASK_FILTER_FIELDS = (
    "q",
    "status",
    "assignee",
    "label",
    "priority",
    "due_before",
    "due_after",
    "created_by",
    "completed",
)


def task_filters_active(params):
    return any(params.get(field) for field in TASK_FILTER_FIELDS)


def _uuid_list(params, field):
    values = []
    for raw in params.getlist(field):
        if not raw:
            continue
        parsed = parse_uuid_or_none(raw)
        if parsed is None:
            raise TaskFilterError(field, "Malformed UUID.")
        values.append(parsed)
    return values


def _date_or_error(params, field):
    raw = params.get(field)
    if not raw:
        return None
    try:
        day = parse_date(raw)
    except ValueError:
        # Well-formed but impossible dates (2026-13-01) raise instead of
        # returning None; both cases are the caller's bug, not a 500.
        day = None
    if day is None:
        raise TaskFilterError(field, "Malformed date.")
    return day


def apply_task_filters(qs, params):
    """Narrow *qs* to the tasks matching the request's query parameters.

    ``status``, ``assignee`` and ``label`` are repeatable; repeated values
    OR together, distinct filters AND. ``assignee`` also accepts the
    literal ``none`` for unassigned tasks. Raises TaskFilterError on
    malformed input. M2M filters can duplicate rows - the caller owns the
    final ``distinct()``.
    """
    statuses = _uuid_list(params, "status")
    if statuses:
        qs = qs.filter(status_id__in=statuses)

    labels = _uuid_list(params, "label")
    if labels:
        qs = qs.filter(labels__in=labels)

    assignee_values = [raw for raw in params.getlist("assignee") if raw]
    if assignee_values:
        ids = []
        unassigned = False
        for raw in assignee_values:
            if raw == "none":
                unassigned = True
                continue
            try:
                ids.append(int(raw))
            except ValueError as exc:
                raise TaskFilterError("assignee", "Invalid user ID.") from exc
        condition = Q()
        if ids:
            condition |= Q(assignees__in=ids)
        if unassigned:
            condition |= Q(assignees__isnull=True)
        qs = qs.filter(condition)

    priority = params.get("priority")
    if priority:
        if priority not in Task.Priority.values:
            raise TaskFilterError("priority", "Unknown priority.")
        qs = qs.filter(priority=priority)

    due_before = _date_or_error(params, "due_before")
    if due_before is not None:
        qs = qs.filter(due_date__lte=due_before)
    due_after = _date_or_error(params, "due_after")
    if due_after is not None:
        qs = qs.filter(due_date__gte=due_after)

    created_by = params.get("created_by")
    if created_by:
        try:
            qs = qs.filter(created_by=int(created_by))
        except ValueError as exc:
            raise TaskFilterError("created_by", "Invalid user ID.") from exc

    completed = params.get("completed")
    if completed:
        done = Q(status__category=TaskStatus.Category.DONE)
        qs = qs.filter(done) if is_truthy(completed) else qs.exclude(done)

    query = params.get("q")
    if query:
        qs = fts_tasks(qs, query)

    return qs


def apply_task_ordering(qs, params):
    """Reorder *qs* per the ``ordering`` parameter, if present.

    Accepts the ORDERABLE_FIELDS names with an optional ``-`` prefix.
    Tasks without a due date always sort last, and created_at/uuid break
    ties so paginated pages never overlap. Without the parameter the
    queryset's own ordering is kept.
    """
    raw = params.get("ordering")
    if not raw:
        return qs
    descending = raw.startswith("-")
    field = raw.removeprefix("-")
    if field not in ORDERABLE_FIELDS:
        raise TaskFilterError("ordering", "Unknown ordering field.")
    if field == "priority":
        qs = qs.annotate(_priority_rank=PRIORITY_RANK)
        field = "_priority_rank"
    expression = (
        F(field).desc(nulls_last=True) if descending else F(field).asc(nulls_last=True)
    )
    return qs.order_by(expression, "created_at", "uuid")

"""The filters of a task list, for the API and for the pages alike.

A board, a backlog and ``GET /projects/tasks`` read the same query string, so
a filtered page is a link that can be shared and the API takes what the page
shows.
"""

from django import forms
from django.db.models import Case, FloatField, Q, Value, When
from django_filters import rest_framework as filters

from workspace.common.filters import (
    BooleanFilter,
    KeyFilter,
    MultiValueFilter,
    OrderingFilter,
)

from .models import Task, TaskStatus
from .queries import PRIORITY_RANK
from .services.search import fts_tasks, reference_q


class InvalidTaskFilter(Exception):
    """A page was asked for with a filter value that cannot be read."""

    def __init__(self, field):
        super().__init__(field)
        self.field = field


class TaskFilter(filters.FilterSet):
    q = filters.CharFilter(method="search")
    status = MultiValueFilter(child=forms.UUIDField())
    status_name = KeyFilter(field_name="status__name")
    assignee = MultiValueFilter(
        field_name="assignees",
        child=forms.IntegerField(),
        allow_none=True,
        allow_me=True,
        distinct=True,
    )
    assignee_username = KeyFilter(field_name="assignees__username", distinct=True)
    label = MultiValueFilter(
        field_name="labels", child=forms.UUIDField(), distinct=True
    )
    label_name = KeyFilter(field_name="labels__name", distinct=True)
    epic = MultiValueFilter(child=forms.UUIDField(), allow_none=True)
    epic_name = KeyFilter(field_name="epic__name")
    priority = MultiValueFilter(child=forms.ChoiceField(choices=Task.Priority.choices))
    created_by = MultiValueFilter(child=forms.IntegerField(), allow_me=True)
    created_by_username = KeyFilter(field_name="created_by__username")
    completed = BooleanFilter(method="filter_completed")
    due = filters.DateFromToRangeFilter(field_name="due_date")

    class Meta:
        model = Task
        fields = []

    def search(self, queryset, name, value):
        """Text matches, plus the task a reference names (WR-42, 42, #42)
        whatever its text says. ``search_rank`` puts that one first."""
        found = fts_tasks(queryset, value)
        named = reference_q(value)
        if named is None:
            return found
        return queryset.filter(Q(pk__in=found.values("pk")) | named).annotate(
            search_rank=Case(
                When(named, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )

    def filter_completed(self, queryset, name, value):
        done = Q(status__category=TaskStatus.Category.DONE)
        return queryset.filter(done) if value else queryset.exclude(done)


class TaskListFilter(TaskFilter):
    """What the API adds to the filters of a page: its order.

    A page keeps the order of its board; a list of the API sorts on
    ``ordering``, and by relevance when it is searched without one. The
    sprint is a filter here only: on a page ``?sprint=`` is the scope of the
    backlog, which the page reads for itself.
    """

    sprint = MultiValueFilter(child=forms.UUIDField(), allow_none=True)
    sprint_name = KeyFilter(field_name="sprint__name")

    ordering = OrderingFilter(
        fields={
            "position": "position",
            "priority": PRIORITY_RANK,
            "due_date": "due_date",
            "estimate": "estimate",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        tie_break=("created_at", "uuid"),
    )

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        asked = self.form.cleaned_data
        if asked.get("q") and not asked.get("ordering"):
            queryset = queryset.order_by("-search_rank", "-created_at", "uuid")
        return queryset


class AllTasksFilter(TaskListFilter):
    """The filters of the list that spans projects."""

    project = MultiValueFilter(child=forms.UUIDField())
    project_key = KeyFilter(field_name="project__key")
    include_archived = BooleanFilter(method="keep")

    def keep(self, queryset, name, value):
        return queryset

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if not self.form.cleaned_data.get("include_archived"):
            queryset = queryset.filter(project__archived_at__isnull=True)
        return queryset


# Query parameters that narrow a task list, as a page reads them.
TASK_FILTER_PARAMETERS = tuple(
    parameter
    for name in TaskFilter.base_filters
    for parameter in ((f"{name}_after", f"{name}_before") if name == "due" else (name,))
)


def task_filters_active(params):
    return any(params.get(parameter) for parameter in TASK_FILTER_PARAMETERS)


def filter_tasks(queryset, request):
    """The tasks of *queryset* a page's query string keeps, in its own order.

    Rows can come back twice where a filter crosses a to-many relation; the
    caller owns the final ``distinct()``. Raises ``InvalidTaskFilter``.
    """
    filterset = TaskFilter(request.GET, queryset=queryset, request=request)
    if not filterset.is_valid():
        raise InvalidTaskFilter(next(iter(filterset.errors)))
    return filterset.qs

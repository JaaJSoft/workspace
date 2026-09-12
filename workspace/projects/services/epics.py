from django.db.models import Count, F, Q

from ..models import TaskStatus


def epics_with_progress(project):
    """The project's epics annotated with their done/total task counts,
    dated epics first by target date, then the undated ones by name.

    One reverse-FK join serves both counts; no distinct needed since no
    other multi-valued relation is joined here. Shared by the API, the
    overview and the timeline so the rollup has one definition.
    """
    return project.epics.annotate(
        task_count=Count("tasks"),
        done_task_count=Count(
            "tasks", filter=Q(tasks__status__category=TaskStatus.Category.DONE)
        ),
    ).order_by(F("target_date").asc(nulls_last=True), "name")

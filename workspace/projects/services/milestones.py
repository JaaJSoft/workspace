from django.db.models import Count, Q

from ..models import TaskStatus


def milestones_with_progress(project):
    """The project's milestones annotated with their done/total task counts.

    One reverse-FK join serves both counts; no distinct needed since no
    other multi-valued relation is joined here. Shared by the API, the
    overview and the timeline so the rollup has one definition.
    """
    return project.milestones.annotate(
        task_count=Count("tasks"),
        done_task_count=Count(
            "tasks", filter=Q(tasks__status__category=TaskStatus.Category.DONE)
        ),
    ).order_by("target_date", "name")

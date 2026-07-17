from workspace.common.search import apply_fulltext
from workspace.common.search.schema import Col, FulltextIndex

from ..models import Project, Task
from ..queries import user_project_ids

PROJECT_FTS = FulltextIndex(
    table="projects_project",
    columns=(Col("name"), Col("description", "C", cap=100_000)),
)

TASK_FTS = FulltextIndex(
    table="projects_task",
    columns=(Col("title"), Col("description", "C", cap=100_000)),
)


def fts_projects(qs, query):
    """Filter qs to full-text matches, annotated with `search_rank`.

    Caller applies order_by.
    """
    return apply_fulltext(qs, query, index=PROJECT_FTS)


def fts_tasks(qs, query):
    return apply_fulltext(qs, query, index=TASK_FTS)


def search_projects_qs(user, query):
    """Ranked project search, access-filtered for `user`.

    Archived projects are excluded: search mirrors what the projects UI
    lists, and archived boards resurface via their own view, not search.
    """
    qs = Project.objects.filter(
        uuid__in=user_project_ids(user),
        archived_at__isnull=True,
    )
    return fts_projects(qs, query).order_by("-search_rank", "-updated_at")


def search_tasks_qs(user, query):
    """Ranked task search across every project the user can access."""
    qs = Task.objects.filter(
        project_id__in=user_project_ids(user),
        project__archived_at__isnull=True,
    )
    return fts_tasks(qs, query).order_by("-search_rank", "-created_at")

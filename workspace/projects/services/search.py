import re

from workspace.common.search import apply_fulltext
from workspace.common.search.schema import Col, FulltextIndex

from ..models import Project, Task
from ..queries import user_project_ids
from .references import REFERENCE_RE

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


NUMBER_RE = re.compile(r"^#?([0-9]{1,9})$")


def reference_tasks_qs(user, query):
    """Exact task matches for a WR-42 reference or a bare number (42, #42).

    Empty queryset when the query is not reference-shaped; access-filtered
    and archived-excluded like the full-text search.
    """
    text = query.strip()
    base = Task.objects.filter(
        project_id__in=user_project_ids(user),
        project__archived_at__isnull=True,
    )
    match = REFERENCE_RE.match(text)
    if match:
        return base.filter(
            project__key=match.group(1).upper(), number=int(match.group(2))
        )
    match = NUMBER_RE.match(text)
    if match:
        return base.filter(number=int(match.group(1)))
    return Task.objects.none()


def combined_task_search(user, query, *, limit, extra_filter=None):
    """Task matches for *query*: exact reference hits (WR-42, 42, #42)
    first, then ranked full-text hits, deduplicated and capped at *limit*.

    *extra_filter* is a Q narrowing both halves the same way (the AI tool
    passes project/assignee/status/due-date constraints). Returns
    ``(tasks, reference_uuids)``: the reference set tells the caller which
    hits matched by reference rather than by content. Every task comes
    with project and status joined.
    """
    ref_qs = reference_tasks_qs(user, query)
    fts_qs = search_tasks_qs(user, query)
    if extra_filter:
        ref_qs = ref_qs.filter(extra_filter)
        fts_qs = fts_qs.filter(extra_filter)
    tasks = list(ref_qs.select_related("project", "status")[:limit])
    reference_uuids = {t.uuid for t in tasks}
    if len(tasks) < limit:
        for task in fts_qs.select_related("project", "status")[:limit]:
            if task.uuid in reference_uuids:
                continue
            tasks.append(task)
            if len(tasks) == limit:
                break
    return tasks, reference_uuids

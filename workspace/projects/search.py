from django.db.models import Q

from workspace.core.module_registry import SearchResult, SearchScope, SearchTag

from .queries import personal_project_ids
from .services.search import combined_task_search, search_projects_qs


def search_projects(query, user, limit, scope=SearchScope.ALL):
    projects = search_projects_qs(user, query)
    if scope == SearchScope.MINE:
        projects = projects.filter(uuid__in=personal_project_ids(user))
    projects = projects[:limit]
    return [
        SearchResult(
            uuid=str(p.uuid),
            name=p.name,
            url=f"/projects/{p.uuid}",
            matched_value=p.name,
            match_type="project",
            type_icon="square-kanban",
            module_slug="projects",
            tags=(SearchTag("Project", "accent"),),
        )
        for p in projects
    ]


def search_project_tasks(query, user, limit, scope=SearchScope.ALL):
    extra_filter = None
    if scope == SearchScope.MINE:
        extra_filter = Q(project_id__in=personal_project_ids(user))
    tasks, reference_uuids = combined_task_search(
        user, query, limit=limit, extra_filter=extra_filter
    )
    return [
        SearchResult(
            uuid=str(t.uuid),
            name=t.title,
            url=f"/projects/{t.project_id}?task={t.uuid}",
            matched_value=t.reference if t.uuid in reference_uuids else t.title,
            match_type="task",
            type_icon="list-todo",
            module_slug="projects",
            tags=(SearchTag(t.project.name, "accent"),),
        )
        for t in tasks
    ]

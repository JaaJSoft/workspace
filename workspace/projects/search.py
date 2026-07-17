from workspace.core.module_registry import SearchResult, SearchTag

from .services.search import search_projects_qs, search_tasks_qs


def search_projects(query, user, limit):
    projects = search_projects_qs(user, query)[:limit]
    return [
        SearchResult(
            uuid=str(p.uuid),
            name=p.name,
            url=f"/projects/{p.uuid}",
            matched_value=p.name,
            match_type="project",
            type_icon="square-kanban",
            module_slug="projects",
            module_color="accent",
            tags=(SearchTag("Project", "accent"),),
        )
        for p in projects
    ]


def search_project_tasks(query, user, limit):
    tasks = search_tasks_qs(user, query).select_related("project")[:limit]
    return [
        SearchResult(
            uuid=str(t.uuid),
            name=t.title,
            url=f"/projects/{t.project_id}",
            matched_value=t.title,
            match_type="task",
            type_icon="list-todo",
            module_slug="projects",
            module_color="accent",
            tags=(SearchTag(t.project.name, "accent"),),
        )
        for t in tasks
    ]

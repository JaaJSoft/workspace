from workspace.core.module_registry import SearchResult, SearchTag

from .services.search import reference_tasks_qs, search_projects_qs, search_tasks_qs


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
    reference_hits = list(
        reference_tasks_qs(user, query).select_related("project")[:limit]
    )
    reference_uuids = {t.uuid for t in reference_hits}
    tasks = list(reference_hits)
    remaining = limit - len(tasks)
    if remaining > 0:
        for task in search_tasks_qs(user, query).select_related("project")[:limit]:
            if task.uuid in reference_uuids:
                continue
            tasks.append(task)
            if len(tasks) == limit:
                break
    return [
        SearchResult(
            uuid=str(t.uuid),
            name=t.title,
            url=f"/projects/{t.project_id}?task={t.uuid}",
            matched_value=t.reference if t.uuid in reference_uuids else t.title,
            match_type="task",
            type_icon="list-todo",
            module_slug="projects",
            module_color="accent",
            tags=(SearchTag(t.project.name, "accent"),),
        )
        for t in tasks
    ]

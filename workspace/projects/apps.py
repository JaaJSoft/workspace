from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.projects"

    def ready(self):
        from workspace.projects.user_settings import ProjectsSettings
        from workspace.users.settings_registry import user_settings_registry

        user_settings_registry.register("projects", ProjectsSettings)

        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name=gettext_lazy("Projects"),
                slug="projects",
                description=gettext_lazy("Plan work with kanban boards and backlogs."),
                icon="square-kanban",
                color="orange",
                url="/projects",
                order=35,
            )
        )

        from workspace.common.search.schema import register_fulltext_index
        from workspace.projects.services.search import PROJECT_FTS, TASK_FTS

        register_fulltext_index(PROJECT_FTS)
        register_fulltext_index(TASK_FTS)

        from workspace.core.module_registry import SearchProviderInfo
        from workspace.projects.search import search_project_tasks, search_projects

        registry.register_search_provider(
            SearchProviderInfo(
                slug="projects",
                module_slug="projects",
                search_fn=search_projects,
                scoped=True,
            )
        )
        registry.register_search_provider(
            SearchProviderInfo(
                slug="project-tasks",
                module_slug="projects",
                search_fn=search_project_tasks,
                scoped=True,
            )
        )

        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.projects.sse_provider import ProjectsSSEProvider

        sse_registry.register(
            SSEProviderInfo(slug="projects", provider_cls=ProjectsSSEProvider)
        )

        from workspace.ai.tool_registry import tool_registry
        from workspace.projects.ai_tools import ProjectsToolProvider

        tool_registry.register_provider(ProjectsToolProvider())

        from workspace.core.activity_registry import (
            ActivityProviderInfo,
            activity_registry,
        )
        from workspace.projects.activity import ProjectsActivityProvider

        activity_registry.register(
            ActivityProviderInfo(
                slug="projects",
                label=gettext_lazy("Projects"),
                icon="square-kanban",
                provider_cls=ProjectsActivityProvider,
            )
        )

        from workspace.core.today_registry import TodaySource, today_registry
        from workspace.projects.today import is_urgent_task, tasks_for_today

        today_registry.register(
            TodaySource(
                slug="tasks",
                module_slug="projects",
                label=gettext_lazy("Tasks"),
                description=gettext_lazy("Your tasks overdue, due today or urgent."),
                icon="list-todo",
                url="/projects",
                items_fn=tasks_for_today,
                template="projects/ui/partials/today_task.html",
                is_urgent=is_urgent_task,
                order=20,
            )
        )

        # The actions register on import, project actions before task
        # actions. Imported here rather than lazily so a broken import fails
        # the boot instead of a worker answering "no actions" forever.
        from workspace.projects.actions import project as project_actions  # noqa: F401
        from workspace.projects.actions import task as task_actions  # noqa: F401

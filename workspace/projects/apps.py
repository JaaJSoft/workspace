from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.projects"

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name="Projects",
                slug="projects",
                description="Plan work with kanban boards and backlogs.",
                icon="square-kanban",
                color="accent",
                url="/projects",
                order=35,
                preview=True,
            )
        )

        from workspace.common.search.schema import register_fulltext_index
        from workspace.projects.services.search import PROJECT_FTS, TASK_FTS

        register_fulltext_index(PROJECT_FTS)
        register_fulltext_index(TASK_FTS)

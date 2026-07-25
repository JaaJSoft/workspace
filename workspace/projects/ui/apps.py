from django.apps import AppConfig


class ProjectsUiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.projects.ui"
    label = "projects_ui"
    verbose_name = "Projects UI"

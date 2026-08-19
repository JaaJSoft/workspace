from django.apps import AppConfig


class ImportsUiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.imports.ui"
    label = "imports_ui"
    verbose_name = "Imports UI"

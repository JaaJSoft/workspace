from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "workspace.core"
    label = "core"

    def ready(self):
        import workspace.core.signals  # noqa: F401

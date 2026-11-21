from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "workspace.core"

    def ready(self):
        from django.core.checks import register

        from workspace.core.checks import check_task_broker

        register(check_task_broker)

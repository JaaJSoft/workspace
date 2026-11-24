from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "workspace.core"

    def ready(self):
        from workspace.core.user_settings import CoreSettings
        from workspace.users.settings_registry import user_settings_registry

        user_settings_registry.register("core", CoreSettings)

        from django.core.checks import register

        from workspace.core.checks import check_task_broker

        register(check_task_broker)

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

        from django.db.models.signals import post_migrate

        from workspace.core.services.sqlite_maintenance import switch_after_migrate

        # Every app with models sends post_migrate, core (which has none)
        # never does: the receiver listens to all of them and returns at once
        # on a database already switched.
        post_migrate.connect(switch_after_migrate, dispatch_uid="sqlite_incremental")

        from django.contrib import admin

        # Unfold swaps admin.site for its own site in its ready(), which runs
        # first: it sits above core in INSTALLED_APPS.
        admin.site.index_template = "core/admin/index.html"

from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.dashboard"

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name=gettext_lazy("Dashboard"),
                slug="dashboard",
                description=gettext_lazy("Overview of your workspace."),
                icon="home",
                color="slate",
                url="/",
                order=0,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name=gettext_lazy("Settings"),
                    keywords=["settings", "preferences", "configuration"],
                    icon="settings",
                    url="/users/settings",
                    kind="navigate",
                    module_slug="dashboard",
                    order=1,
                ),
                CommandInfo(
                    name=gettext_lazy("My profile"),
                    keywords=["profile", "account", "avatar"],
                    icon="user",
                    url="/users/profile",
                    kind="navigate",
                    module_slug="dashboard",
                    order=2,
                ),
            ]
        )

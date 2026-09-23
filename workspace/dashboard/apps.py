from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.dashboard"

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name="Dashboard",
                slug="dashboard",
                description="Overview of your workspace.",
                icon="home",
                color="slate",
                url="/",
                order=0,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Settings",
                    keywords=["settings", "preferences", "configuration"],
                    icon="settings",
                    url="/users/settings",
                    kind="navigate",
                    module_slug="dashboard",
                    order=1,
                ),
                CommandInfo(
                    name="My profile",
                    keywords=["profile", "account", "avatar"],
                    icon="user",
                    url="/users/profile",
                    kind="navigate",
                    module_slug="dashboard",
                    order=2,
                ),
            ]
        )

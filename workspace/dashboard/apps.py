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

        planned_modules = [
            ModuleInfo(
                name="Contacts",
                slug="contacts",
                description="Manage contacts and interactions.",
                icon="contact",
                color="fuchsia",
                url=None,
                active=False,
                order=60,
            ),
            ModuleInfo(
                name="Bookmarks",
                slug="bookmarks",
                description="Save and organize links.",
                icon="bookmark",
                color="yellow",
                url=None,
                active=False,
                order=70,
            ),
        ]
        for module in planned_modules:
            registry.register(module)

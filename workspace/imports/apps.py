from django.apps import AppConfig


class ImportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.imports"

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, registry

        # A real module (page, commands, notifications) that is used once when
        # arriving rather than lived in, so it stays off the home dashboard.
        registry.register(
            ModuleInfo(
                name="Imports",
                slug="imports",
                description="Bring your files over from another cloud.",
                icon="download-cloud",
                color="accent",
                url="/imports",
                order=90,
                preview=True,
                show_on_dashboard=False,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Import from another cloud",
                    keywords=["import", "migrate", "nextcloud", "webdav", "cloud"],
                    icon="download-cloud",
                    color="accent",
                    url="/imports?new=1",
                    kind="navigate",
                    module_slug="imports",
                    order=90,
                ),
                CommandInfo(
                    name="My imports",
                    keywords=["imports", "migration", "jobs"],
                    icon="download-cloud",
                    color="accent",
                    url="/imports",
                    kind="navigate",
                    module_slug="imports",
                    order=91,
                ),
            ]
        )

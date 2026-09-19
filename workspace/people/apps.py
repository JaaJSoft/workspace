from django.apps import AppConfig


class PeopleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.people"

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name="People",
                slug="people",
                description="Keep the people around your data in one address book.",
                icon="contact",
                color="secondary",
                url="/people",
                order=30,
            )
        )

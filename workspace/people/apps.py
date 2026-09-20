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
                color="rose",
                url="/people",
                order=32,
            )
        )

        from workspace.core.module_registry import CommandInfo, SearchProviderInfo

        # Registers the actions at boot; a broken import fails the boot
        # instead of a worker answering "no actions" forever.
        from workspace.people.actions import person as person_actions  # noqa: F401
        from workspace.people.search import search_persons

        registry.register_search_provider(
            SearchProviderInfo(
                slug="people", module_slug="people", search_fn=search_persons
            )
        )
        registry.register_commands(
            [
                CommandInfo(
                    name="People",
                    keywords=["people", "contacts", "address book", "carnet"],
                    icon="contact",
                    color="secondary",
                    url="/people",
                    kind="navigate",
                    module_slug="people",
                    order=35,
                ),
                CommandInfo(
                    name="New contact",
                    keywords=["new contact", "new person", "add contact"],
                    icon="user-plus",
                    color="secondary",
                    url="/people?action=new-person",
                    kind="action",
                    module_slug="people",
                    order=36,
                ),
            ]
        )

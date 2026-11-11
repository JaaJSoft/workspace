from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class NotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.notes"

    def ready(self):
        from workspace.core.activity_registry import (
            ActivityProviderInfo,
            activity_registry,
        )
        from workspace.core.module_registry import (
            CommandInfo,
            ModuleInfo,
            SearchProviderInfo,
            registry,
        )
        from workspace.notes.activity import NotesActivityProvider
        from workspace.notes.search import search_notes

        registry.register(
            ModuleInfo(
                name=gettext_lazy("Notes"),
                slug="notes",
                description=gettext_lazy("Write and organize markdown notes."),
                icon="notebook-pen",
                color="emerald",
                url="/notes",
                order=30,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="notes",
                module_slug="notes",
                search_fn=search_notes,
                refines=("files",),
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name=gettext_lazy("Notes"),
                    keywords=["notes", "markdown", "writing"],
                    icon="notebook-pen",
                    url="/notes",
                    kind="navigate",
                    module_slug="notes",
                    order=30,
                ),
                CommandInfo(
                    name=gettext_lazy("New note"),
                    keywords=["new note", "create note"],
                    icon="file-plus",
                    url="/notes?action=new",
                    kind="action",
                    module_slug="notes",
                    order=31,
                ),
                CommandInfo(
                    name=gettext_lazy("Today's journal"),
                    keywords=["journal", "daily", "today", "diary"],
                    icon="book-open",
                    url="/notes?view=journal",
                    kind="action",
                    module_slug="notes",
                    order=32,
                ),
                CommandInfo(
                    name=gettext_lazy("Favorite notes"),
                    keywords=["favorites", "starred notes"],
                    icon="star",
                    url="/notes?view=favorites",
                    kind="navigate",
                    module_slug="notes",
                    order=33,
                ),
                CommandInfo(
                    name=gettext_lazy("Recent notes"),
                    keywords=["recent notes", "last edited"],
                    icon="clock",
                    url="/notes?view=recent",
                    kind="navigate",
                    module_slug="notes",
                    order=34,
                ),
            ]
        )

        activity_registry.register(
            ActivityProviderInfo(
                slug="notes",
                label=gettext_lazy("Notes"),
                icon="notebook-pen",
                provider_cls=NotesActivityProvider,
            )
        )

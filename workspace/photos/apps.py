from django.apps import AppConfig


class PhotosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.photos"

    def ready(self):
        from workspace.core.module_registry import (
            CommandInfo,
            ModuleInfo,
            SearchProviderInfo,
            registry,
        )
        from workspace.photos.search import search_photos

        # Imported for the @on_file_event side effect, here rather than
        # lazily so a broken import fails the boot instead of leaving uploads
        # silently unanalyzed.
        from workspace.photos.services import handlers  # noqa: F401

        registry.register(
            ModuleInfo(
                name="Photos",
                slug="photos",
                description="Browse your pictures by the day they were taken.",
                icon="images",
                color="lime",
                url="/photos",
                order=12,
                preview=True,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="photos",
                module_slug="photos",
                search_fn=search_photos,
                refines=("files",),
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Photos",
                    keywords=["photos", "pictures", "images", "timeline", "gallery"],
                    icon="images",
                    url="/photos",
                    kind="navigate",
                    module_slug="photos",
                    order=12,
                ),
                CommandInfo(
                    name="Favorite photos",
                    keywords=["favorites", "starred photos", "best pictures"],
                    icon="star",
                    url="/photos?favorites=1",
                    kind="navigate",
                    module_slug="photos",
                    order=13,
                ),
                CommandInfo(
                    name="Undated photos",
                    keywords=["undated", "no date", "screenshots", "photos"],
                    icon="calendar-x",
                    url="/photos?date=undated",
                    kind="navigate",
                    module_slug="photos",
                    order=14,
                ),
            ]
        )

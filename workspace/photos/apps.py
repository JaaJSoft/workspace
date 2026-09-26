from django.apps import AppConfig


class PhotosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.photos"

    def ready(self):
        from workspace.common.vectors.schema import register_vector_index
        from workspace.core.module_registry import (
            CommandInfo,
            ModuleInfo,
            SearchProviderInfo,
            registry,
        )
        from workspace.files.properties_sections import (
            PropertiesSection,
            properties_section_registry,
        )
        from workspace.people.sections import PersonSection
        from workspace.people.sections import (
            section_registry as person_section_registry,
        )
        from workspace.photos import signals  # noqa: F401

        # The album and face actions register on import. Imported here rather than
        # lazily so a broken import fails the boot instead of a worker
        # answering "no actions" forever.
        from workspace.photos.actions import album as album_actions  # noqa: F401
        from workspace.photos.actions import face as face_actions  # noqa: F401
        from workspace.photos.indexes import FACE_EMBEDDINGS
        from workspace.photos.queries import has_photos_of_person
        from workspace.photos.search import search_photos

        # Imported for the @on_file_event side effect, here rather than
        # lazily so a broken import fails the boot instead of leaving uploads
        # silently unanalyzed.
        from workspace.photos.services import handlers  # noqa: F401
        from workspace.photos.services.details import (
            is_section_visible,
            section_context,
        )

        register_vector_index(FACE_EMBEDDINGS)

        registry.register(
            ModuleInfo(
                name="Photos",
                slug="photos",
                description="Browse your photos and videos by the day they were taken.",
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

        # A contact's page in People lists the photos they are in, through
        # the viewer's own face clusters: people never learns about faces.
        person_section_registry.register(
            PersonSection(
                slug="photos",
                label="Photos",
                icon="images",
                template="photos/ui/partials/person_photos_section.html",
                order=50,
                is_visible=has_photos_of_person,
            )
        )

        properties_section_registry.register(
            PropertiesSection(
                slug="photo",
                label="Photo",
                template="photos/ui/partials/properties_section.html",
                is_visible=is_section_visible,
                get_context=section_context,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Photos",
                    keywords=[
                        "photos",
                        "pictures",
                        "images",
                        "videos",
                        "timeline",
                        "gallery",
                    ],
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
                    name="Videos",
                    keywords=["videos", "movies", "clips", "recordings"],
                    icon="video",
                    url="/photos?type=video",
                    kind="navigate",
                    module_slug="photos",
                    order=14,
                ),
                CommandInfo(
                    name="Undated photos",
                    keywords=["undated", "no date", "screenshots", "photos"],
                    icon="calendar-x",
                    url="/photos?date=undated",
                    kind="navigate",
                    module_slug="photos",
                    order=15,
                ),
            ]
        )

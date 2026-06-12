from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.files"

    def ready(self):
        from workspace.core.module_registry import (
            CommandInfo,
            ModuleInfo,
            SearchProviderInfo,
            registry,
        )
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.files.search import search_files
        from workspace.files.sse_provider import FilesSSEProvider

        registry.register(
            ModuleInfo(
                name="Files",
                slug="files",
                description="Store, organize and share files.",
                icon="hard-drive",
                color="primary",
                url="/files",
                order=10,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="files",
                module_slug="files",
                search_fn=search_files,
            )
        )

        sse_registry.register(
            SSEProviderInfo(
                slug="files",
                provider_cls=FilesSSEProvider,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Files",
                    keywords=["files", "documents", "storage"],
                    icon="hard-drive",
                    color="primary",
                    url="/files",
                    kind="navigate",
                    module_slug="files",
                    order=10,
                ),
                CommandInfo(
                    name="Favorite files",
                    keywords=["favorites", "starred", "bookmarked"],
                    icon="star",
                    color="primary",
                    url="/files?favorites=1",
                    kind="navigate",
                    module_slug="files",
                    order=11,
                ),
                CommandInfo(
                    name="Recent files",
                    keywords=["recent", "last opened", "history"],
                    icon="clock",
                    color="primary",
                    url="/files?recent=1",
                    kind="navigate",
                    module_slug="files",
                    order=12,
                ),
                CommandInfo(
                    name="Trash",
                    keywords=["trash", "deleted", "recycle bin"],
                    icon="trash-2",
                    color="primary",
                    url="/files/trash",
                    kind="navigate",
                    module_slug="files",
                    order=13,
                ),
                CommandInfo(
                    name="Shared with me",
                    keywords=["shared", "sharing", "received"],
                    icon="share-2",
                    color="primary",
                    url="/files?shared=1",
                    kind="navigate",
                    module_slug="files",
                    order=14,
                ),
            ]
        )

        from workspace.core.activity_registry import (
            ActivityProviderInfo,
            activity_registry,
        )
        from workspace.files.activity import FilesActivityProvider

        activity_registry.register(
            ActivityProviderInfo(
                slug="files",
                label="Files",
                icon="hard-drive",
                color="primary",
                provider_cls=FilesActivityProvider,
            )
        )

        from workspace.ai.tool_registry import tool_registry
        from workspace.files.ai_tools import FilesToolProvider

        tool_registry.register_provider(FilesToolProvider())

        # Register Prometheus metrics + storage-bytes collector.
        from workspace.files import metrics  # noqa: F401

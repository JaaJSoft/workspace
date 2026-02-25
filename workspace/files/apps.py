from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.files'

    def ready(self):
        from django.db.models.signals import post_save, post_delete
        from workspace.core.module_registry import CommandInfo, ModuleInfo, SearchProviderInfo, registry
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.files.search import search_files
        from workspace.files.sse_provider import FilesSSEProvider

        registry.register(ModuleInfo(
            name='Files',
            slug='files',
            description='Store, organize and share files.',
            icon='hard-drive',
            color='primary',
            url='/files',
            order=10,
        ))

        registry.register_search_provider(SearchProviderInfo(
            slug='files',
            module_slug='files',
            search_fn=search_files,
        ))

        sse_registry.register(SSEProviderInfo(
            slug='files',
            provider_cls=FilesSSEProvider,
        ))

        registry.register_commands([
            CommandInfo(
                name='Files', keywords=['files', 'documents', 'storage'],
                icon='hard-drive', color='primary', url='/files',
                kind='navigate', module_slug='files', order=10,
            ),
        ])

        from .models import MimeTypeRule
        from .services.mime import invalidate_cache

        def _invalidate(sender, **kwargs):
            invalidate_cache()

        post_save.connect(_invalidate, sender=MimeTypeRule)
        post_delete.connect(_invalidate, sender=MimeTypeRule)

from django.apps import AppConfig


class AIConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.ai'

    def ready(self):
        from workspace.ai.sse_provider import AISSEProvider
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry

        sse_registry.register(SSEProviderInfo(
            slug='ai',
            provider_cls=AISSEProvider,
        ))

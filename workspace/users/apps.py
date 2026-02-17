from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.users'

    def ready(self):
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.users.sse_provider import PresenceSSEProvider

        sse_registry.register(SSEProviderInfo(
            slug='presence',
            provider_cls=PresenceSSEProvider,
        ))

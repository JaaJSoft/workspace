from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.notifications'

    def ready(self):
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.notifications.sse_provider import NotificationsSSEProvider

        sse_registry.register(SSEProviderInfo(
            slug='notifications',
            provider_cls=NotificationsSSEProvider,
        ))

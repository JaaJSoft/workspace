from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.users'

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.users.sse_provider import PresenceSSEProvider

        sse_registry.register(SSEProviderInfo(
            slug='presence',
            provider_cls=PresenceSSEProvider,
        ))

        user_logged_out.connect(self._on_logout)

        from workspace.ai.tool_registry import tool_registry
        from workspace.users.ai_tools import UsersToolProvider

        tool_registry.register_provider(UsersToolProvider())

    @staticmethod
    def _on_logout(sender, request, user, **kwargs):
        if user and user.is_authenticated:
            from workspace.users.services import presence as presence_service
            presence_service.clear(user.id)

from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.chat'

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, PendingActionProviderInfo, SearchProviderInfo, registry
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry
        from workspace.chat.search import search_conversations
        from workspace.chat.sse_provider import ChatSSEProvider

        registry.register(ModuleInfo(
            name='Chat',
            slug='chat',
            description='Real-time messaging with direct and group conversations.',
            icon='message-circle',
            color='info',
            url='/chat',
            order=15,
        ))

        registry.register_search_provider(SearchProviderInfo(
            slug='chat',
            module_slug='chat',
            search_fn=search_conversations,
        ))

        sse_registry.register(SSEProviderInfo(
            slug='chat',
            provider_cls=ChatSSEProvider,
        ))

        def _chat_pending_actions(user):
            from workspace.chat.services import get_unread_counts
            return get_unread_counts(user).get('total', 0)

        registry.register_pending_action_provider(PendingActionProviderInfo(
            module_slug='chat',
            pending_action_fn=_chat_pending_actions,
        ))

        registry.register_commands([
            CommandInfo(
                name='Chat', keywords=['chat', 'messages', 'conversations'],
                icon='message-circle', color='info', url='/chat',
                kind='navigate', module_slug='chat', order=15,
            ),
            CommandInfo(
                name='New conversation', keywords=['new chat', 'message'],
                icon='message-circle-plus', color='info', url='/chat?action=new',
                kind='action', module_slug='chat', order=16,
            ),
        ])

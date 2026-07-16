from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.chat"

    def ready(self):
        from workspace.chat.services.message_search import CHAT_FTS
        from workspace.common.search.schema import register_fulltext_index

        register_fulltext_index(CHAT_FTS)

        from workspace.chat.search import search_chat_messages, search_conversations
        from workspace.chat.sse_provider import ChatSSEProvider
        from workspace.core.module_registry import (
            CommandInfo,
            ModuleInfo,
            PendingActionProviderInfo,
            SearchProviderInfo,
            registry,
        )
        from workspace.core.sse_registry import SSEProviderInfo, sse_registry

        registry.register(
            ModuleInfo(
                name="Chat",
                slug="chat",
                description="Real-time messaging with direct and group conversations.",
                icon="message-circle",
                color="info",
                url="/chat",
                order=15,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="chat",
                module_slug="chat",
                search_fn=search_conversations,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="chat-messages",
                module_slug="chat",
                search_fn=search_chat_messages,
            )
        )

        sse_registry.register(
            SSEProviderInfo(
                slug="chat",
                provider_cls=ChatSSEProvider,
            )
        )

        def _chat_pending_actions(user):
            from workspace.chat.services.conversations import get_unread_counts

            return get_unread_counts(user).get("total", 0)

        registry.register_pending_action_provider(
            PendingActionProviderInfo(
                module_slug="chat",
                pending_action_fn=_chat_pending_actions,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Chat",
                    keywords=["chat", "messages", "conversations"],
                    icon="message-circle",
                    color="info",
                    url="/chat",
                    kind="navigate",
                    module_slug="chat",
                    order=15,
                ),
                CommandInfo(
                    name="New conversation",
                    keywords=["new chat", "message"],
                    icon="message-circle-plus",
                    color="info",
                    url="/chat?action=new",
                    kind="action",
                    module_slug="chat",
                    order=16,
                ),
            ]
        )

        from workspace.chat.activity import ChatActivityProvider
        from workspace.core.activity_registry import (
            ActivityProviderInfo,
            activity_registry,
        )

        activity_registry.register(
            ActivityProviderInfo(
                slug="chat",
                label="Chat",
                icon="message-circle",
                color="info",
                provider_cls=ChatActivityProvider,
            )
        )

        from workspace.ai.tool_registry import tool_registry
        from workspace.chat.ai_tools import ChatToolProvider

        tool_registry.register_provider(ChatToolProvider())

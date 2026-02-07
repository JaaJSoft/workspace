from django.urls import path

from . import views, views_sse

urlpatterns = [
    # Conversations
    path(
        'api/v1/chat/conversations',
        views.ConversationListView.as_view(),
        name='chat-conversations',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>',
        views.ConversationDetailView.as_view(),
        name='chat-conversation-detail',
    ),
    # Messages
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/messages',
        views.MessageListView.as_view(),
        name='chat-messages',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>',
        views.MessageDetailView.as_view(),
        name='chat-message-detail',
    ),
    # Reactions
    path(
        'api/v1/chat/messages/<uuid:message_id>/reactions',
        views.ReactionToggleView.as_view(),
        name='chat-reaction-toggle',
    ),
    # Read / Unread
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/read',
        views.MarkReadView.as_view(),
        name='chat-mark-read',
    ),
    path(
        'api/v1/chat/unread-counts',
        views.UnreadCountsView.as_view(),
        name='chat-unread-counts',
    ),
    # SSE
    path(
        'api/v1/chat/stream',
        views_sse.chat_stream,
        name='chat-stream',
    ),
]

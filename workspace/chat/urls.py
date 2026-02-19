from django.urls import path

from . import views

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
    # Members
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/members',
        views.ConversationMembersView.as_view(),
        name='chat-conversation-members',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/members/<int:user_id>',
        views.ConversationMemberRemoveView.as_view(),
        name='chat-conversation-member-remove',
    ),
    # Messages
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/messages/search',
        views.ConversationMessageSearchView.as_view(),
        name='chat-message-search',
    ),
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
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>/readers',
        views.MessageReadersView.as_view(),
        name='chat-message-readers',
    ),
    # Reactions
    path(
        'api/v1/chat/messages/<uuid:message_id>/reactions',
        views.ReactionToggleView.as_view(),
        name='chat-reaction-toggle',
    ),
    # Stats
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/stats',
        views.ConversationStatsView.as_view(),
        name='chat-conversation-stats',
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
    # Group avatars
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/avatar',
        views.GroupAvatarUploadView.as_view(),
        name='chat-group-avatar-upload',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/avatar/image',
        views.GroupAvatarRetrieveView.as_view(),
        name='chat-group-avatar-retrieve',
    ),
    # Pinning (pin-reorder before uuid patterns to avoid ambiguity)
    path(
        'api/v1/chat/conversations/pin-reorder',
        views.ConversationPinReorderView.as_view(),
        name='chat-conversation-pin-reorder',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/pin',
        views.ConversationPinView.as_view(),
        name='chat-conversation-pin',
    ),
    # Message pinning
    path(
        'api/v1/chat/messages/<uuid:message_id>/pin',
        views.MessagePinToggleView.as_view(),
        name='chat-message-pin-toggle',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/pinned-messages',
        views.ConversationPinnedMessagesView.as_view(),
        name='chat-conversation-pinned-messages',
    ),
    # Attachments
    path(
        'api/v1/chat/attachments/<uuid:attachment_id>',
        views.AttachmentDownloadView.as_view(),
        name='chat-attachment-download',
    ),
    path(
        'api/v1/chat/attachments/<uuid:attachment_id>/save-to-files',
        views.AttachmentSaveToFilesView.as_view(),
        name='chat-attachment-save-to-files',
    ),
]

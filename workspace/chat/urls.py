from django.urls import path

from . import (
    views,
    views_attachments,
    views_avatar,
    views_bots,
    views_interactions,
    views_messages,
    views_pins,
    views_scheduled,
    views_search,
    views_typing,
)

urlpatterns = [
    # Conversations
    path(
        "api/v1/chat/conversations",
        views.ConversationListView.as_view(),
        name="chat-conversations",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>",
        views.ConversationDetailView.as_view(),
        name="chat-conversation-detail",
    ),
    # Members
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/members",
        views.ConversationMembersView.as_view(),
        name="chat-conversation-members",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/members/<int:user_id>",
        views.ConversationMemberRemoveView.as_view(),
        name="chat-conversation-member-remove",
    ),
    # Messages
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/search",
        views_search.ConversationMessageSearchView.as_view(),
        name="chat-message-search",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages",
        views_messages.MessageListView.as_view(),
        name="chat-messages",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>",
        views_messages.MessageDetailView.as_view(),
        name="chat-message-detail",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>/readers",
        views_messages.MessageReadersView.as_view(),
        name="chat-message-readers",
    ),
    # Reactions
    path(
        "api/v1/chat/messages/<uuid:message_id>/reactions",
        views_messages.ReactionToggleView.as_view(),
        name="chat-reaction-toggle",
    ),
    # Stats
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/stats",
        views_search.ConversationStatsView.as_view(),
        name="chat-conversation-stats",
    ),
    # Media gallery
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/medias",
        views_search.ConversationMediaView.as_view(),
        name="chat-conversation-media",
    ),
    # Read / Unread
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/read",
        views_messages.MarkReadView.as_view(),
        name="chat-mark-read",
    ),
    path(
        "api/v1/chat/unread-counts",
        views_typing.UnreadCountsView.as_view(),
        name="chat-unread-counts",
    ),
    # Typing indicator
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/typing",
        views_typing.TypingIndicatorView.as_view(),
        name="chat-typing",
    ),
    # Group avatars
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/avatar",
        views_avatar.GroupAvatarUploadView.as_view(),
        name="chat-group-avatar-upload",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/avatar/image",
        views_avatar.GroupAvatarRetrieveView.as_view(),
        name="chat-group-avatar-retrieve",
    ),
    # Pinning (pin-reorder before uuid patterns to avoid ambiguity)
    path(
        "api/v1/chat/conversations/pin-reorder",
        views_pins.ConversationPinReorderView.as_view(),
        name="chat-conversation-pin-reorder",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/pin",
        views_pins.ConversationPinView.as_view(),
        name="chat-conversation-pin",
    ),
    # Message pinning
    path(
        "api/v1/chat/messages/<uuid:message_id>/pin",
        views_pins.MessagePinToggleView.as_view(),
        name="chat-message-pin-toggle",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/pinned-messages",
        views_pins.ConversationPinnedMessagesView.as_view(),
        name="chat-conversation-pinned-messages",
    ),
    # Interactive AI questions
    path(
        "api/v1/chat/messages/<uuid:message_id>/answer",
        views_interactions.MessageInteractionAnswerView.as_view(),
        name="chat-message-interaction-answer",
    ),
    # Clear conversation
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/clear",
        views_typing.ConversationClearView.as_view(),
        name="chat-conversation-clear",
    ),
    # Scheduled messages
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/schedules",
        views_scheduled.ScheduledMessageListView.as_view(),
        name="chat-scheduled-messages",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/schedules/<uuid:schedule_id>",
        views_scheduled.ScheduledMessageDetailView.as_view(),
        name="chat-scheduled-message-detail",
    ),
    # Bot retry
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>/retry",
        views_bots.BotRetryView.as_view(),
        name="chat-bot-retry",
    ),
    # Bot cancel
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/bot-cancel",
        views_bots.BotCancelView.as_view(),
        name="chat-bot-cancel",
    ),
    # Attachments
    path(
        "api/v1/chat/attachments/<uuid:attachment_id>",
        views_attachments.AttachmentDownloadView.as_view(),
        name="chat-attachment-download",
    ),
    path(
        "api/v1/chat/attachments/<uuid:attachment_id>/save-to-files",
        views_attachments.AttachmentSaveToFilesView.as_view(),
        name="chat-attachment-save-to-files",
    ),
]

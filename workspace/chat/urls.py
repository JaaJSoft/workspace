from django.urls import path

from .views import (
    attachments,
    avatar,
    bots,
    calls,
    conversations,
    goals,
    interactions,
    messages,
    pins,
    scheduled,
    search,
    threads,
    typing,
)

urlpatterns = [
    # Conversations
    path(
        "api/v1/chat/conversations",
        conversations.ConversationListView.as_view(),
        name="chat-conversations",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>",
        conversations.ConversationDetailView.as_view(),
        name="chat-conversation-detail",
    ),
    # Threads
    path(
        "api/v1/chat/threads/<uuid:root_uuid>/read",
        threads.ThreadReadView.as_view(),
        name="chat-thread-read",
    ),
    # Members
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/members",
        conversations.ConversationMembersView.as_view(),
        name="chat-conversation-members",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/members/<int:user_id>",
        conversations.ConversationMemberRemoveView.as_view(),
        name="chat-conversation-member-remove",
    ),
    # Per-member notification level
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/notification-level",
        conversations.ConversationNotificationLevelView.as_view(),
        name="chat-conversation-notification-level",
    ),
    # Messages
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/search",
        search.ConversationMessageSearchView.as_view(),
        name="chat-message-search",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages",
        messages.MessageListView.as_view(),
        name="chat-messages",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>",
        messages.MessageDetailView.as_view(),
        name="chat-message-detail",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>/readers",
        messages.MessageReadersView.as_view(),
        name="chat-message-readers",
    ),
    # Reactions
    path(
        "api/v1/chat/messages/<uuid:message_id>/reactions",
        messages.ReactionToggleView.as_view(),
        name="chat-reaction-toggle",
    ),
    # Stats
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/stats",
        search.ConversationStatsView.as_view(),
        name="chat-conversation-stats",
    ),
    # Media gallery
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/media",
        search.ConversationMediaView.as_view(),
        name="chat-conversation-media",
    ),
    # Read / Unread
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/read",
        messages.MarkReadView.as_view(),
        name="chat-mark-read",
    ),
    path(
        "api/v1/chat/unread-counts",
        typing.UnreadCountsView.as_view(),
        name="chat-unread-counts",
    ),
    # Typing indicator
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/typing",
        typing.TypingIndicatorView.as_view(),
        name="chat-typing",
    ),
    # Calls
    # Call connection diagnostic (user-scoped, no conversation)
    path(
        "api/v1/chat/call/diagnostic/signal",
        calls.CallDiagnosticSignalView.as_view(),
        name="chat-call-diagnostic-signal",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/call",
        calls.CallStateView.as_view(),
        name="chat-call-state",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/call/join",
        calls.CallJoinView.as_view(),
        name="chat-call-join",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/call/leave",
        calls.CallLeaveView.as_view(),
        name="chat-call-leave",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/call/signal",
        calls.CallSignalView.as_view(),
        name="chat-call-signal",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/call/heartbeat",
        calls.CallHeartbeatView.as_view(),
        name="chat-call-heartbeat",
    ),
    # Group avatars
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/avatar",
        avatar.GroupAvatarUploadView.as_view(),
        name="chat-group-avatar-upload",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/avatar/image",
        avatar.GroupAvatarRetrieveView.as_view(),
        name="chat-group-avatar-retrieve",
    ),
    # Pinning (pin-reorder before uuid patterns to avoid ambiguity)
    path(
        "api/v1/chat/conversations/pin-reorder",
        pins.ConversationPinReorderView.as_view(),
        name="chat-conversation-pin-reorder",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/pin",
        pins.ConversationPinView.as_view(),
        name="chat-conversation-pin",
    ),
    # Message pinning
    path(
        "api/v1/chat/messages/<uuid:message_id>/pin",
        pins.MessagePinToggleView.as_view(),
        name="chat-message-pin-toggle",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/pinned-messages",
        pins.ConversationPinnedMessagesView.as_view(),
        name="chat-conversation-pinned-messages",
    ),
    # Interactive AI questions
    path(
        "api/v1/chat/messages/<uuid:message_id>/answer",
        interactions.MessageInteractionAnswerView.as_view(),
        name="chat-message-interaction-answer",
    ),
    # Clear conversation
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/clear",
        typing.ConversationClearView.as_view(),
        name="chat-conversation-clear",
    ),
    # Scheduled messages
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/schedules",
        scheduled.ScheduledMessageListView.as_view(),
        name="chat-scheduled-messages",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/schedules/<uuid:schedule_id>",
        scheduled.ScheduledMessageDetailView.as_view(),
        name="chat-scheduled-message-detail",
    ),
    # Agent goals
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/goals",
        goals.AgentGoalListView.as_view(),
        name="chat-agent-goals",
    ),
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/goals/<uuid:goal_id>",
        goals.AgentGoalDetailView.as_view(),
        name="chat-agent-goal-detail",
    ),
    # Bot retry
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/messages/<uuid:message_id>/retry",
        bots.BotRetryView.as_view(),
        name="chat-bot-retry",
    ),
    # Bot cancel
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/bot-cancel",
        bots.BotCancelView.as_view(),
        name="chat-bot-cancel",
    ),
    # Regenerate AI conversation title
    path(
        "api/v1/chat/conversations/<uuid:conversation_id>/regenerate-title",
        bots.ConversationRegenerateTitleView.as_view(),
        name="chat-conversation-regenerate-title",
    ),
    # Attachments
    path(
        "api/v1/chat/attachments/<uuid:attachment_id>",
        attachments.AttachmentDownloadView.as_view(),
        name="chat-attachment-download",
    ),
    path(
        "api/v1/chat/attachments/<uuid:attachment_id>/save-to-files",
        attachments.AttachmentSaveToFilesView.as_view(),
        name="chat-attachment-save-to-files",
    ),
]

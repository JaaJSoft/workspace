from django.db.models import Q, Value, CharField
from django.db.models.functions import Concat

from workspace.core.module_registry import SearchResult, SearchTag
from workspace.chat.models import Conversation, ConversationMember


def search_conversations(query, user, limit):
    # Conversations the user is an active member of
    user_conv_uuids = (
        ConversationMember.objects
        .filter(user=user, left_at__isnull=True)
        .values_list('conversation_id', flat=True)
    )

    # Groups: match on title
    groups = (
        Conversation.objects
        .filter(
            uuid__in=user_conv_uuids,
            kind=Conversation.Kind.GROUP,
            title__icontains=query,
        )
        .order_by('-updated_at')[:limit]
    )

    # DMs: match on the other member's username or full name
    dm_members = (
        ConversationMember.objects
        .filter(
            conversation__uuid__in=user_conv_uuids,
            conversation__kind=Conversation.Kind.DM,
            left_at__isnull=True,
        )
        .exclude(user=user)
        .select_related('user', 'conversation')
        .annotate(
            full_name=Concat('user__first_name', Value(' '), 'user__last_name', output_field=CharField()),
        )
        .filter(
            Q(user__username__icontains=query) | Q(full_name__icontains=query),
        )
        .order_by('-conversation__updated_at')[:limit]
    )

    results = []

    for conv in groups:
        results.append(SearchResult(
            uuid=str(conv.uuid),
            name=conv.title,
            url=f'/chat/{conv.uuid}',
            matched_value=conv.title,
            match_type='title',
            type_icon='users',
            module_slug='chat',
            module_color='info',
            tags=(SearchTag('Group', 'info'),),
        ))

    for member in dm_members:
        username = member.user.username
        full_name = f'{member.user.first_name} {member.user.last_name}'.strip()
        display = full_name or username
        results.append(SearchResult(
            uuid=str(member.conversation_id),
            name=username,
            url=f'/chat/{member.conversation_id}',
            matched_value=display,
            match_type='member',
            type_icon='message-circle',
            module_slug='chat',
            module_color='info',
            tags=(SearchTag('DM', 'info'),),
        ))

    # Sort combined results by relevance isn't trivial here,
    # just return up to limit
    return results[:limit]

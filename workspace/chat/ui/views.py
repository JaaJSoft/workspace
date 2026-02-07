import json

from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Prefetch, Subquery
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.serializers import ConversationListSerializer
from workspace.chat.services import get_unread_counts


def _build_conversation_context(user):
    """Build conversation list with display data for templates."""
    member_convos = ConversationMember.objects.filter(
        user=user, left_at__isnull=True,
    ).values_list('conversation_id', flat=True)

    conversations = (
        Conversation.objects.filter(uuid__in=member_convos)
        .prefetch_related(
            Prefetch(
                'members',
                queryset=ConversationMember.objects.filter(
                    left_at__isnull=True,
                ).select_related('user'),
            ),
        )
        .order_by('-updated_at')
    )

    last_msg_subquery = (
        Message.objects.filter(
            conversation=OuterRef('pk'), deleted_at__isnull=True,
        ).order_by('-created_at').values('uuid')[:1]
    )
    conversations = conversations.annotate(_last_msg_id=Subquery(last_msg_subquery))
    conv_list = list(conversations)

    last_msg_ids = [c._last_msg_id for c in conv_list if c._last_msg_id]
    last_msgs = {
        m.uuid: m
        for m in Message.objects.filter(uuid__in=last_msg_ids).select_related('author')
    }

    unread_data = get_unread_counts(user)
    unread_map = unread_data.get('conversations', {})

    now = timezone.now()
    for c in conv_list:
        c._last_message = last_msgs.get(c._last_msg_id)
        c.unread_count = unread_map.get(str(c.uuid), 0)

        # Resolve display name
        active_members = list(c.members.all())
        other_members = [m for m in active_members if m.user_id != user.id]

        if c.title:
            c.display_name = c.title
        elif c.kind == Conversation.Kind.DM and other_members:
            c.display_name = other_members[0].user.username
        else:
            names = [m.user.username for m in other_members[:3]]
            c.display_name = ', '.join(names) if names else 'Group'

        # Avatar
        if c.kind == Conversation.Kind.DM and other_members:
            c.avatar_initial = other_members[0].user.username[0].upper()
        else:
            initials = [m.user.username[0].upper() for m in other_members[:2]]
            c.avatar_initial = ''.join(initials) or 'G'

        # Last message preview & time ago
        if c._last_message:
            body = c._last_message.body
            if len(body) > 30:
                body = body[:30] + '\u2026'
            c.last_message_preview = f'{c._last_message.author.username}: {body}'
            diff = (now - c._last_message.created_at).total_seconds()
            if diff < 60:
                c.time_ago = 'now'
            elif diff < 3600:
                c.time_ago = f'{int(diff // 60)}m'
            elif diff < 86400:
                c.time_ago = f'{int(diff // 3600)}h'
            elif diff < 604800:
                c.time_ago = f'{int(diff // 86400)}d'
            else:
                c.time_ago = c._last_message.created_at.strftime('%b %d')
        else:
            c.last_message_preview = 'No messages yet'
            c.time_ago = ''

    return conv_list


@login_required
@ensure_csrf_cookie
def chat_view(request):
    """Main chat page with server-rendered conversation list."""
    conv_list = _build_conversation_context(request.user)
    serializer = ConversationListSerializer(conv_list, many=True)

    return render(request, 'chat/ui/index.html', {
        'conversations': conv_list,
        'dm_conversations': [c for c in conv_list if c.kind == Conversation.Kind.DM],
        'group_conversations': [c for c in conv_list if c.kind == Conversation.Kind.GROUP],
        'conversations_json': json.dumps(serializer.data),
    })


@login_required
def conversation_list_view(request):
    """Partial: conversation list HTML for alpine-ajax refresh."""
    conv_list = _build_conversation_context(request.user)
    return render(request, 'chat/ui/partials/conversation_list.html', {
        'dm_conversations': [c for c in conv_list if c.kind == Conversation.Kind.DM],
        'group_conversations': [c for c in conv_list if c.kind == Conversation.Kind.GROUP],
    })

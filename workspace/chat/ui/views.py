import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Prefetch, Subquery
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.chat.models import Conversation, ConversationMember, Message, PinnedConversation, PinnedMessage
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
        for m in Message.objects.filter(uuid__in=last_msg_ids).select_related('author').prefetch_related('attachments')
    }

    unread_data = get_unread_counts(user)
    unread_map = unread_data.get('conversations', {})

    # Build pin map: {conversation_uuid: position}
    pin_map = {
        str(p.conversation_id): p.position
        for p in PinnedConversation.objects.filter(owner=user)
    }

    now = timezone.now()
    for c in conv_list:
        c._last_message = last_msgs.get(c._last_msg_id)
        c.unread_count = unread_map.get(str(c.uuid), 0)

        # Pin data
        pin_pos = pin_map.get(str(c.uuid))
        c.is_pinned = pin_pos is not None
        c.pin_position = pin_pos if pin_pos is not None else None

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
            c.other_user = other_members[0].user
        else:
            initials = [m.user.username[0].upper() for m in other_members[:2]]
            c.avatar_initial = ''.join(initials) or 'G'
            c.other_user = None

        # Last message preview & time ago
        if c._last_message:
            body = c._last_message.body
            if body:
                if len(body) > 30:
                    body = body[:30] + '\u2026'
                c.last_message_preview = f'{c._last_message.author.username}: {body}'
            elif (att := list(c._last_message.attachments.all())):
                label = 'sent a file' if len(att) == 1 else f'sent {len(att)} files'
                c.last_message_preview = f'{c._last_message.author.username}: {label}'
            else:
                c.last_message_preview = f'{c._last_message.author.username}: '
            diff = (now - c._last_message.created_at).total_seconds()
            if diff < 60:
                c.time_ago = 'now'
            elif diff < 3600:
                c.time_ago = f'{int(diff // 60)}m'
            elif diff < 86400:
                c.time_ago = f'{int(diff // 3600)}h'
            elif diff < 604800:
                c.time_ago = f'{int(diff // 86400)}d'
            elif diff < 31536000:
                c.time_ago = c._last_message.created_at.strftime('%b %d')
            else:
                c.time_ago = c._last_message.created_at.strftime("%b '%y")
        else:
            c.last_message_preview = 'No messages yet'
            c.time_ago = ''

    return conv_list


@login_required
@ensure_csrf_cookie
def chat_view(request, conversation_uuid=None):
    """Main chat page with server-rendered conversation list."""
    conv_list = _build_conversation_context(request.user)
    serializer = ConversationListSerializer(conv_list, many=True)

    pinned = sorted(
        [c for c in conv_list if c.is_pinned],
        key=lambda c: (c.pin_position or 0, c.created_at),
    )
    pinned_uuids = {str(c.uuid) for c in pinned}

    return render(request, 'chat/ui/index.html', {
        'conversations': conv_list,
        'pinned_conversations': pinned,
        'dm_conversations': [c for c in conv_list if c.kind == Conversation.Kind.DM and str(c.uuid) not in pinned_uuids],
        'group_conversations': [c for c in conv_list if c.kind == Conversation.Kind.GROUP and str(c.uuid) not in pinned_uuids],
        'conversations_json': json.dumps(serializer.data),
        'initial_conversation_uuid': str(conversation_uuid) if conversation_uuid else '',
    })


@login_required
def conversation_list_view(request):
    """Partial: conversation list HTML for alpine-ajax refresh."""
    conv_list = _build_conversation_context(request.user)

    pinned = sorted(
        [c for c in conv_list if c.is_pinned],
        key=lambda c: (c.pin_position or 0, c.created_at),
    )
    pinned_uuids = {str(c.uuid) for c in pinned}

    return render(request, 'chat/ui/partials/conversation_list.html', {
        'pinned_conversations': pinned,
        'dm_conversations': [c for c in conv_list if c.kind == Conversation.Kind.DM and str(c.uuid) not in pinned_uuids],
        'group_conversations': [c for c in conv_list if c.kind == Conversation.Kind.GROUP and str(c.uuid) not in pinned_uuids],
    })


def group_messages(messages, current_user):
    """Group consecutive messages by same author within 5 min, with date separators.

    Returns a list of dicts:
      {'type': 'date', 'date': date_obj}
      {'type': 'messages', 'author': user, 'is_own': bool, 'messages': [msg, ...]}
    """
    groups = []
    current_date = None
    current_group = None

    for msg in messages:
        msg_date = timezone.localdate(msg.created_at)

        # Insert date separator when the day changes
        if msg_date != current_date:
            if current_group:
                groups.append(current_group)
                current_group = None
            groups.append({'type': 'date', 'date': msg_date, 'datetime': msg.created_at})
            current_date = msg_date

        # Check if this message continues the current group
        can_group = (
            current_group
            and current_group['author'].id == msg.author_id
            and not msg.deleted_at
            and not (current_group['messages'][-1].deleted_at)
            and (msg.created_at - current_group['messages'][-1].created_at) < timedelta(minutes=5)
        )

        if can_group:
            current_group['messages'].append(msg)
        else:
            if current_group:
                groups.append(current_group)
            current_group = {
                'type': 'messages',
                'author': msg.author,
                'is_own': msg.author_id == current_user.id,
                'messages': [msg],
            }

    if current_group:
        groups.append(current_group)

    return groups


@login_required
def conversation_messages_view(request, conversation_uuid):
    """Partial: server-rendered grouped messages for a conversation."""
    membership = ConversationMember.objects.filter(
        conversation_id=conversation_uuid,
        user=request.user,
        left_at__isnull=True,
    ).first()
    if not membership:
        return HttpResponseForbidden()

    qs = (
        Message.objects
        .filter(conversation_id=conversation_uuid)
        .select_related('author', 'reply_to', 'reply_to__author')
        .prefetch_related('reactions__user', 'attachments')
        .order_by('-created_at')
    )

    before = request.GET.get('before')
    if before:
        try:
            cursor_msg = Message.objects.get(uuid=before)
            qs = qs.filter(created_at__lt=cursor_msg.created_at)
        except Message.DoesNotExist:
            pass

    limit = 50
    messages_page = list(qs[:limit + 1])
    has_more = len(messages_page) > limit
    messages_page = messages_page[:limit]
    messages_page.reverse()  # Back to chronological order

    groups = group_messages(messages_page, request.user)

    first_uuid = str(messages_page[0].uuid) if messages_page else ''

    pinned_message_ids = set(
        PinnedMessage.objects.filter(conversation_id=conversation_uuid).values_list('message_id', flat=True)
    )

    return render(request, 'chat/ui/partials/message_list.html', {
        'groups': groups,
        'has_more': has_more,
        'first_uuid': first_uuid,
        'current_user': request.user,
        'pinned_message_ids': pinned_message_ids,
    })

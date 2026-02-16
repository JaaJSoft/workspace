import json
import time
import uuid
from datetime import timedelta
import logging

from django.core.cache import cache
from django.http import StreamingHttpResponse
from django.utils import timezone
from prometheus_client import Gauge

from .models import ConversationMember, Message, PinnedMessage, Reaction
from .serializers import MessageSerializer
from .services import get_unread_counts


logger = logging.getLogger(__name__)

SSE_CONNECTIONS = Gauge(
    'chat_sse_active_connections',
    'Number of active SSE connections',
)


def chat_stream(request):
    """SSE endpoint for real-time chat updates."""
    if not request.user.is_authenticated:
        return StreamingHttpResponse('', status=403)

    response = StreamingHttpResponse(
        _event_stream(request),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache, no-transform'
    response['X-Accel-Buffering'] = 'no'
    # Prevent compression middleware from buffering
    response.streaming = True
    response['Content-Encoding'] = 'identity'
    return response


def _event_stream(request):
    user = request.user
    user_id = user.id

    # Track what we've already sent
    last_event_id = request.META.get('HTTP_LAST_EVENT_ID')
    last_check = time.time()
    last_cache_value = None
    last_keepalive = time.time()
    last_unread_push = 0
    start_time = time.time()

    # Get conversations user is a member of
    member_conv_ids = set(
        ConversationMember.objects.filter(
            user_id=user_id,
            left_at__isnull=True,
        ).values_list('conversation_id', flat=True)
    )

    # Track last seen message/edit/delete/reaction timestamps
    if last_event_id:
        try:
            msg = Message.objects.get(uuid=last_event_id)
            since = msg.created_at
        except Message.DoesNotExist:
            since = timezone.now()
    else:
        since = timezone.now()

    seen_message_ids = set()
    seen_edit_keys = set()
    seen_delete_keys = set()
    seen_reaction_ids = set()
    seen_pin_ids = set()

    # Send initial unread counts immediately
    try:
        unread = get_unread_counts(user)
        yield _format_sse('unread', unread)
        last_unread_push = time.time()
    except Exception:
        logger.exception("Failed to send initial unread counts for user %s", user_id)

    SSE_CONNECTIONS.inc()
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > 60:
                # Timeout — client EventSource will reconnect
                return

            now = time.time()

            # Keepalive every 15 seconds
            if now - last_keepalive >= 15:
                yield ':keepalive\n\n'
                last_keepalive = now

            # Unread counts every 10 seconds
            if now - last_unread_push >= 10:
                try:
                    unread = get_unread_counts(user)
                    yield _format_sse('unread', unread)
                except Exception:
                    logger.exception("Failed to send periodic unread counts for user %s", user_id)
                last_unread_push = now

            # Check cache to see if there are new events
            if now - last_check >= 2:
                last_check = now
                cache_key = f'chat:last_event:{user_id}'
                cache_value = cache.get(cache_key)

                if cache_value and cache_value != last_cache_value:
                    last_cache_value = cache_value

                    # Refresh member conversation IDs
                    member_conv_ids = set(
                        ConversationMember.objects.filter(
                            user_id=user_id,
                            left_at__isnull=True,
                        ).values_list('conversation_id', flat=True)
                    )

                    # New messages
                    new_messages = list(
                        Message.objects.filter(
                            conversation_id__in=member_conv_ids,
                            created_at__gt=since,
                            deleted_at__isnull=True,
                        )
                        .exclude(author_id=user_id)
                        .exclude(uuid__in=seen_message_ids)
                        .select_related('author')
                        .prefetch_related('attachments')
                        .order_by('created_at')[:50]
                    )
                    for msg in new_messages:
                        seen_message_ids.add(msg.uuid)
                        data = {
                            'type': 'message',
                            'conversation_id': str(msg.conversation_id),
                            'message': MessageSerializer(msg).data,
                        }
                        yield _format_sse('message', data, event_id=str(msg.uuid))
                        since = max(since, msg.created_at)

                    # Edited messages
                    edited_messages = (
                        Message.objects.filter(
                            conversation_id__in=member_conv_ids,
                            edited_at__isnull=False,
                            edited_at__gt=since - timedelta(seconds=5),
                        )
                        .exclude(author_id=user_id)
                        .select_related('author')
                        .order_by('edited_at')[:50]
                    )

                    for msg in edited_messages:
                        edit_key = f'{msg.uuid}:{msg.edited_at.isoformat()}'
                        if edit_key in seen_edit_keys:
                            continue
                        seen_edit_keys.add(edit_key)
                        data = {
                            'type': 'message_edited',
                            'conversation_id': str(msg.conversation_id),
                            'message_id': str(msg.uuid),
                            'body': msg.body,
                            'body_html': msg.body_html,
                            'edited_at': msg.edited_at.isoformat(),
                        }
                        yield _format_sse('message_edited', data)

                    # Deleted messages
                    deleted_messages = (
                        Message.objects.filter(
                            conversation_id__in=member_conv_ids,
                            deleted_at__isnull=False,
                            deleted_at__gt=since - timedelta(seconds=5),
                        )
                        .exclude(author_id=user_id)
                        .order_by('deleted_at')[:50]
                    )

                    for msg in deleted_messages:
                        del_key = str(msg.uuid)
                        if del_key in seen_delete_keys:
                            continue
                        seen_delete_keys.add(del_key)
                        data = {
                            'type': 'message_deleted',
                            'conversation_id': str(msg.conversation_id),
                            'message_id': str(msg.uuid),
                        }
                        yield _format_sse('message_deleted', data)

                    # Reactions
                    new_reactions = (
                        Reaction.objects.filter(
                            message__conversation_id__in=member_conv_ids,
                            created_at__gt=since - timedelta(seconds=5),
                        )
                        .exclude(user_id=user_id)
                        .exclude(uuid__in=seen_reaction_ids)
                        .select_related('user', 'message')
                        .order_by('created_at')[:50]
                    )

                    for reaction in new_reactions:
                        seen_reaction_ids.add(reaction.uuid)
                        data = {
                            'type': 'reaction',
                            'conversation_id': str(reaction.message.conversation_id),
                            'message_id': str(reaction.message.uuid),
                            'emoji': reaction.emoji,
                            'user': {
                                'id': reaction.user.id,
                                'username': reaction.user.username,
                            },
                            'action': 'added',
                        }
                        yield _format_sse('reaction', data)

                    # Pinned messages
                    new_pins = (
                        PinnedMessage.objects.filter(
                            conversation_id__in=member_conv_ids,
                            created_at__gt=since - timedelta(seconds=5),
                        )
                        .exclude(pinned_by_id=user_id)
                        .exclude(uuid__in=seen_pin_ids)
                        .select_related('pinned_by')
                        .order_by('created_at')[:50]
                    )

                    for pin in new_pins:
                        seen_pin_ids.add(pin.uuid)
                        data = {
                            'type': 'message_pinned',
                            'conversation_id': str(pin.conversation_id),
                            'message_id': str(pin.message_id),
                            'pinned_by': {
                                'id': pin.pinned_by.id,
                                'username': pin.pinned_by.username,
                            },
                        }
                        yield _format_sse('message_pinned', data)

            time.sleep(1)
    finally:
        SSE_CONNECTIONS.dec()


class _SSEEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        return super().default(obj)


def _format_sse(event, data, event_id=None):
    """Format an SSE event string."""
    lines = []
    lines.append(f'event: {event}')
    if event_id:
        lines.append(f'id: {event_id}')
    lines.append(f'data: {json.dumps(data, cls=_SSEEncoder)}')
    lines.append('')
    lines.append('')
    return '\n'.join(lines)

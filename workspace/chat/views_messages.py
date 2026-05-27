import logging
import mimetypes

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.db.models.functions import Greatest
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none
from .models import Conversation, ConversationMember, Message, MessageAttachment, PinnedMessage, Reaction
from .serializers import (
    MessageCreateSerializer,
    MessageEditSerializer,
    MessageSerializer,
    ReactionToggleSerializer,
)
from .services.conversations import get_active_membership
from .services.notifications import notify_conversation_members, notify_new_message
from .services.rendering import extract_mentions, render_message_body
from ..common.logging import scrub

User = get_user_model()
logger = logging.getLogger(__name__)


@extend_schema(tags=['Chat'])
class MessageListView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]

    @extend_schema(
        summary="List messages in a conversation",
        parameters=[
            OpenApiParameter(name='before', type=str, required=False),
            OpenApiParameter(name='limit', type=int, required=False),
        ],
    )
    def get(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = min(max(int(request.query_params.get('limit', 50)), 1), 100)
        except (TypeError, ValueError):
            limit = 50
        before = request.query_params.get('before')

        messages = Message.objects.filter(
            conversation_id=conversation_id,
        ).select_related(
            'author', 'author__bot_profile',
            'reply_to', 'reply_to__author',
            'interaction', 'interaction__interacted_by',
        ).prefetch_related(
            Prefetch(
                'reactions',
                queryset=Reaction.objects.select_related('user'),
            ),
            'attachments',
            'link_previews__preview',
        )

        if before:
            before_uuid = parse_uuid_or_none(before)
            if before_uuid is None:
                # Malformed cursor: treat as "no cursor" instead of letting
                # UUIDField.to_python raise ValidationError -> 500.
                logger.debug('Ignoring malformed ?before cursor: %s', scrub(before))
            else:
                # Scope the cursor lookup to the current conversation: an
                # unrestricted Message.objects.get(uuid=...) would let a caller
                # use a UUID from another conversation as a cursor and read its
                # created_at via the resulting page boundary (cross-conversation
                # timing oracle).
                cursor_msg = (
                    Message.objects
                    .filter(conversation_id=conversation_id, uuid=before_uuid)
                    .only('created_at')
                    .first()
                )
                if cursor_msg is not None:
                    messages = messages.filter(created_at__lt=cursor_msg.created_at)
                else:
                    # Unknown cursor (no such UUID, or not in this conversation):
                    # treat as "no cursor" and return the most recent page.
                    logger.debug('Ignoring unknown ?before cursor: %s', scrub(before))

        # Get limit+1 to check has_more
        messages = messages.order_by('-created_at')[:limit + 1]
        messages = list(messages)

        has_more = len(messages) > limit
        if has_more:
            messages = messages[:limit]

        # Return in ascending order
        messages.reverse()

        serializer = MessageSerializer(messages, many=True)
        return Response({
            'messages': serializer.data,
            'has_more': has_more,
        })

    @extend_schema(
        summary="Send a message",
        request=MessageCreateSerializer,
    )
    def post(self, request, conversation_id):
        from workspace.files.services.files import FileService

        # Lazy import to avoid circular dependency with views.py
        from .views import _trigger_bot_response

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        body = serializer.validated_data.get('body', '').strip()
        files = request.FILES.getlist('files')
        file_uuids = serializer.validated_data.get('file_uuids', [])

        if not body and not files and not file_uuids:
            return Response(
                {'detail': 'Message must have text or at least one file.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(files) + len(file_uuids) > 10:
            return Response(
                {'detail': 'Maximum 10 files per message.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_file_size = 50 * 1024 * 1024  # 50 MB
        for f in files:
            if f.size > max_file_size:
                return Response(
                    {'detail': f'File "{f.name}" exceeds the 50 MB limit.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        picked_files = []
        if file_uuids:
            file_uuids = list(dict.fromkeys(file_uuids))
            from workspace.files.models import File as WorkspaceFile
            qs = WorkspaceFile.objects.filter(
                uuid__in=file_uuids,
                node_type=WorkspaceFile.NodeType.FILE,
                deleted_at__isnull=True,
            )
            accessible = [
                f for f in qs
                if FileService.can_access(request.user, f)
            ]
            if len(accessible) != len(file_uuids):
                return Response(
                    {'detail': 'One or more files not found or not accessible.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            picked_files = accessible

        # Extract mentions and resolve to real usernames
        mention_map = {}
        mentioned_user_ids = set()
        has_everyone = False
        if body:
            raw_mentions, has_everyone = extract_mentions(body)
            if raw_mentions:
                mentioned_users = User.objects.filter(
                    username__in=raw_mentions
                ).values_list('id', 'username')
                mention_map = {uname: uid for uid, uname in mentioned_users}
                mentioned_user_ids = set(mention_map.values())
            if has_everyone:
                mention_map['everyone'] = None

        body_html = render_message_body(body, mention_map=mention_map or None) if body else ''

        reply_to = None
        reply_to_uuid = serializer.validated_data.get('reply_to_uuid')
        if reply_to_uuid:
            try:
                reply_to = Message.objects.get(
                    uuid=reply_to_uuid,
                    conversation_id=conversation_id,
                    deleted_at__isnull=True,
                )
            except Message.DoesNotExist:
                return Response(
                    {'detail': 'Reply target message not found in this conversation.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            with transaction.atomic():
                message = Message.objects.create(
                    conversation_id=conversation_id,
                    author=request.user,
                    body=body,
                    body_html=body_html,
                    reply_to=reply_to,
                )

                for f in files:
                    mime_type = getattr(f, 'content_type', None) or ''
                    if not mime_type or mime_type == 'application/octet-stream':
                        mime_type = mimetypes.guess_type(f.name or '')[0] or 'application/octet-stream'
                    MessageAttachment.objects.create(
                        message=message,
                        file=f,
                        original_name=f.name,
                        mime_type=mime_type,
                        size=f.size,
                    )

                from django.core.files.base import File as DjangoFile
                for ws_file in picked_files:
                    attachment = MessageAttachment(
                        message=message,
                        original_name=ws_file.name,
                        mime_type=ws_file.mime_type or mimetypes.guess_type(ws_file.name or '')[0] or 'application/octet-stream',
                        size=ws_file.size or 0,
                    )
                    with ws_file.content.open('rb') as f:
                        attachment.file = DjangoFile(f, name=ws_file.name)
                        attachment.save()

                # Increment unread_count for other active members
                ConversationMember.objects.filter(
                    conversation_id=conversation_id,
                    left_at__isnull=True,
                ).exclude(user=request.user).update(
                    unread_count=F('unread_count') + 1,
                )

                # Bump conversation updated_at
                Conversation.objects.filter(pk=conversation_id).update(
                    updated_at=timezone.now(),
                )
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Workspace file content unavailable: %s", scrub(str(exc)))
            return Response(
                {'detail': 'One or more workspace file contents are unavailable.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Notify other members via SSE + push notifications
        conversation = Conversation.objects.get(pk=conversation_id)
        notify_conversation_members(
            conversation, exclude_user=request.user,
        )
        notify_new_message(conversation, request.user, body, mentioned_user_ids=mentioned_user_ids, mention_everyone=has_everyone)

        # Clear typing indicator now that the message is sent
        from .services.typing import clear_typing
        clear_typing(conversation_id, request.user.id)

        # Trigger AI response if a bot is in the conversation
        _trigger_bot_response(conversation_id, message, request.user)

        # Enqueue link preview fetching for URLs in the message body
        if body:
            from .services.link_preview import extract_urls
            urls = extract_urls(body)
            if urls:
                from .tasks import fetch_link_previews
                fetch_link_previews.delay(str(message.pk), urls)

        msg = (
            Message.objects.filter(pk=message.pk)
            .select_related(
                'author', 'author__bot_profile',
                'reply_to', 'reply_to__author',
                'interaction', 'interaction__interacted_by',
            )
            .prefetch_related(
                Prefetch(
                    'reactions',
                    queryset=Reaction.objects.select_related('user'),
                ),
                'attachments',
                'link_previews__preview',
            )
            .first()
        )
        return Response(
            MessageSerializer(msg).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Chat'])
class MessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Edit a message",
        request=MessageEditSerializer,
    )
    def patch(self, request, conversation_id, message_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            message = Message.objects.select_related(
                'conversation',
            ).get(
                uuid=message_id,
                conversation_id=conversation_id,
                deleted_at__isnull=True,
            )
        except Message.DoesNotExist:
            return Response(
                {'detail': 'Message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if message.author_id != request.user.id:
            return Response(
                {'detail': 'Only the author can edit this message.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MessageEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message.body = serializer.validated_data['body']

        # Extract mentions for rendering
        body = message.body
        raw_mentions, has_everyone = extract_mentions(body)
        mention_map = {}
        if raw_mentions:
            mentioned_users = User.objects.filter(
                username__in=raw_mentions
            ).values_list('id', 'username')
            mention_map = {uname: uid for uid, uname in mentioned_users}
        if has_everyone:
            mention_map['everyone'] = None
        message.body_html = render_message_body(body, mention_map=mention_map or None)

        message.edited_at = timezone.now()
        message.save(update_fields=['body', 'body_html', 'edited_at'])

        notify_conversation_members(
            message.conversation, exclude_user=request.user,
        )

        # Refetch with prefetches for serialization
        message = (
            Message.objects.filter(pk=message.pk)
            .select_related(
                'author', 'author__bot_profile',
                'reply_to', 'reply_to__author',
                'interaction', 'interaction__interacted_by',
            )
            .prefetch_related(
                Prefetch(
                    'reactions',
                    queryset=Reaction.objects.select_related('user'),
                ),
                'attachments',
                'link_previews__preview',
            )
            .first()
        )
        return Response(MessageSerializer(message).data)

    @extend_schema(summary="Delete a message (soft)")
    @transaction.atomic
    def delete(self, request, conversation_id, message_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            # Reject already-soft-deleted rows so a repeat DELETE doesn't run
            # the unread-decrement block twice and corrupt counts.
            message = Message.objects.select_related(
                'conversation', 'author__bot_profile',
            ).get(
                uuid=message_id,
                conversation_id=conversation_id,
                deleted_at__isnull=True,
            )
        except Message.DoesNotExist:
            return Response(
                {'detail': 'Message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_author = message.author_id == request.user.id
        is_bot_message = hasattr(message.author, 'bot_profile')
        if not is_author and not is_bot_message:
            return Response(
                {'detail': 'Only the author can delete this message.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        message.deleted_at = timezone.now()
        message.save(update_fields=['deleted_at'])

        # Decrement unread_count for members who hadn't read this message
        ConversationMember.objects.filter(
            conversation_id=message.conversation_id,
            left_at__isnull=True,
            unread_count__gt=0,
        ).filter(
            Q(last_read_at__isnull=True) | Q(last_read_at__lt=message.created_at),
        ).exclude(user=message.author).update(
            unread_count=Greatest(F('unread_count') - 1, 0),
        )

        PinnedMessage.objects.filter(message=message).delete()

        notify_conversation_members(
            message.conversation, exclude_user=request.user,
        )

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat'])
class ReactionToggleView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Toggle a reaction on a message",
        request=ReactionToggleSerializer,
    )
    def post(self, request, message_id):
        serializer = ReactionToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            message = Message.objects.select_related('conversation').get(
                uuid=message_id,
                deleted_at__isnull=True,
            )
        except Message.DoesNotExist:
            return Response(
                {'detail': 'Message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = get_active_membership(request.user, message.conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        emoji = serializer.validated_data['emoji']

        existing = Reaction.objects.filter(
            message=message,
            user=request.user,
            emoji=emoji,
        ).first()

        if existing:
            existing.delete()
            action = 'removed'
        else:
            Reaction.objects.create(
                message=message,
                user=request.user,
                emoji=emoji,
            )
            action = 'added'

        notify_conversation_members(
            message.conversation, exclude_user=request.user,
        )

        return Response({
            'action': action,
            'emoji': emoji,
            'message_id': str(message.uuid),
        })


@extend_schema(tags=['Chat'])
class MessageReadersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get who has read a specific message")
    def get(self, request, conversation_id, message_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            message = Message.objects.get(
                uuid=message_id,
                conversation_id=conversation_id,
                deleted_at__isnull=True,
            )
        except Message.DoesNotExist:
            return Response(
                {'detail': 'Message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        members = ConversationMember.objects.filter(
            conversation_id=conversation_id,
            left_at__isnull=True,
        ).exclude(user=message.author).select_related('user')

        readers = []
        not_read = []
        for m in members:
            if m.last_read_at and m.last_read_at >= message.created_at:
                readers.append({
                    'user_id': m.user.id,
                    'username': m.user.username,
                    'read_at': m.last_read_at.isoformat(),
                })
            else:
                not_read.append({
                    'user_id': m.user.id,
                    'username': m.user.username,
                })

        return Response({
            'readers': readers,
            'not_read': not_read,
            'total_members': len(readers) + len(not_read),
            'read_count': len(readers),
        })


@extend_schema(tags=['Chat'])
class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark conversation as read")
    @transaction.atomic
    def post(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        update_fields = []
        if membership.unread_count > 0 or membership.last_read_at is None:
            membership.last_read_at = timezone.now()
            membership.unread_count = 0
            update_fields = ['last_read_at', 'unread_count']

        if update_fields:
            membership.save(update_fields=update_fields)

        # Mark any unread chat notification for this conversation as read
        from workspace.notifications.models import Notification
        marked = Notification.objects.filter(
            recipient=request.user,
            origin='chat',
            url=f'/chat/{conversation_id}',
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        if marked:
            from workspace.core.sse_registry import notify_sse
            notify_sse('notifications', request.user.id)

        notify_conversation_members(
            Conversation(pk=conversation_id), exclude_user=request.user,
        )

        return Response({'status': 'ok'})

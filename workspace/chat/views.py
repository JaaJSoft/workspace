from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db.models import Count, F, Max, Min, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Greatest
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import avatar_service as group_avatar_service
from .models import Conversation, ConversationMember, Message, MessageAttachment, PinnedConversation, PinnedMessage, Reaction
from .serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    MessageCreateSerializer,
    MessageEditSerializer,
    MessageSerializer,
    PinnedMessageSerializer,
    ReactionToggleSerializer,
)
from .services import (
    get_or_create_dm,
    get_unread_counts,
    notify_conversation_members,
    notify_new_message,
    render_message_body,
)

User = get_user_model()


def _get_active_membership(user, conversation_id):
    """Return the active membership or None."""
    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user=user,
        left_at__isnull=True,
    ).first()


@extend_schema(tags=['Chat'])
class ConversationListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List active conversations")
    def get(self, request):
        user = request.user

        # Get conversation IDs where user is an active member
        member_convos = ConversationMember.objects.filter(
            user=user,
            left_at__isnull=True,
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

        # Compute unread counts
        unread_data = get_unread_counts(user)
        unread_map = unread_data.get('conversations', {})

        # Prefetch last message per conversation
        last_msg_subquery = (
            Message.objects.filter(
                conversation=OuterRef('pk'),
                deleted_at__isnull=True,
            )
            .order_by('-created_at')
            .values('uuid')[:1]
        )
        conversations = conversations.annotate(
            _last_msg_id=Subquery(last_msg_subquery),
        )

        # Fetch last messages in bulk
        conv_list = list(conversations)
        last_msg_ids = [c._last_msg_id for c in conv_list if c._last_msg_id]
        last_msgs = {
            m.uuid: m
            for m in Message.objects.filter(uuid__in=last_msg_ids).select_related('author').prefetch_related('attachments')
        }

        # Build pin map
        pin_map = {
            str(p.conversation_id): p.position
            for p in PinnedConversation.objects.filter(owner=user)
        }

        for c in conv_list:
            c._last_message = last_msgs.get(c._last_msg_id)
            c.unread_count = unread_map.get(str(c.uuid), 0)
            pin_pos = pin_map.get(str(c.uuid))
            c.is_pinned = pin_pos is not None
            c.pin_position = pin_pos

        serializer = ConversationListSerializer(conv_list, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Create a conversation",
        request=ConversationCreateSerializer,
    )
    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        member_ids = serializer.validated_data['member_ids']
        title = serializer.validated_data.get('title', '')

        # Validate that all member_ids exist
        users = User.objects.filter(id__in=member_ids)
        if users.count() != len(member_ids):
            return Response(
                {'detail': 'One or more user IDs are invalid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # DM: exactly one other user
        if len(member_ids) == 1:
            other_user = users.first()
            if other_user.id == request.user.id:
                return Response(
                    {'detail': 'Cannot create a DM with yourself.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            conversation = get_or_create_dm(request.user, other_user)
        else:
            # Group conversation
            conversation = Conversation.objects.create(
                kind=Conversation.Kind.GROUP,
                title=title,
                created_by=request.user,
            )
            # Add creator + selected members
            members_to_create = [
                ConversationMember(conversation=conversation, user=request.user),
            ]
            for u in users:
                if u.id != request.user.id:
                    members_to_create.append(
                        ConversationMember(conversation=conversation, user=u),
                    )
            ConversationMember.objects.bulk_create(members_to_create)

        # Refetch with prefetched members
        conversation = (
            Conversation.objects.filter(pk=conversation.pk)
            .prefetch_related(
                Prefetch(
                    'members',
                    queryset=ConversationMember.objects.filter(
                        left_at__isnull=True,
                    ).select_related('user'),
                ),
            )
            .first()
        )
        return Response(
            ConversationDetailSerializer(conversation).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Chat'])
class ConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get conversation detail")
    def get(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = (
            Conversation.objects.filter(pk=conversation_id)
            .prefetch_related(
                Prefetch(
                    'members',
                    queryset=ConversationMember.objects.filter(
                        left_at__isnull=True,
                    ).select_related('user'),
                ),
            )
            .first()
        )
        return Response(ConversationDetailSerializer(conversation).data)

    @extend_schema(summary="Update conversation details")
    def patch(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        update_fields = []

        # Title update (groups only)
        if 'title' in request.data:
            if conversation.kind != Conversation.Kind.GROUP:
                return Response(
                    {'detail': 'Only group conversations can be renamed.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            title = request.data['title'].strip() if request.data['title'] else ''
            if not title:
                return Response(
                    {'detail': 'Title cannot be empty.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            conversation.title = title
            update_fields.append('title')

        # Description update (all conversation types)
        if 'description' in request.data:
            conversation.description = (request.data['description'] or '').strip()
            update_fields.append('description')

        if not update_fields:
            return Response(
                {'detail': 'No fields to update. Provide title or description.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        conversation.save(update_fields=update_fields)
        return Response({
            'uuid': str(conversation.uuid),
            'title': conversation.title,
            'description': conversation.description,
        })

    @extend_schema(summary="Leave conversation")
    def delete(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        membership.left_at = timezone.now()
        membership.save(update_fields=['left_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat'])
class MessageListView(APIView):
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
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        limit = min(int(request.query_params.get('limit', 50)), 100)
        before = request.query_params.get('before')

        messages = Message.objects.filter(
            conversation_id=conversation_id,
        ).select_related('author', 'reply_to', 'reply_to__author').prefetch_related(
            Prefetch(
                'reactions',
                queryset=Reaction.objects.select_related('user'),
            ),
            'attachments',
        )

        if before:
            try:
                cursor_msg = Message.objects.get(uuid=before)
                messages = messages.filter(created_at__lt=cursor_msg.created_at)
            except Message.DoesNotExist:
                pass

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

        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        body = serializer.validated_data.get('body', '').strip()
        files = request.FILES.getlist('files')

        if not body and not files:
            return Response(
                {'detail': 'Message must have text or at least one file.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(files) > 10:
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

        body_html = render_message_body(body) if body else ''

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

        message = Message.objects.create(
            conversation_id=conversation_id,
            author=request.user,
            body=body,
            body_html=body_html,
            reply_to=reply_to,
        )

        for f in files:
            mime_type = FileService.infer_mime_type(f.name, uploaded=f)
            MessageAttachment.objects.create(
                message=message,
                file=f,
                original_name=f.name,
                mime_type=mime_type,
                size=f.size,
            )

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

        # Notify other members via SSE + push notifications
        conversation = Conversation.objects.get(pk=conversation_id)
        notify_conversation_members(
            conversation, exclude_user=request.user,
        )
        notify_new_message(conversation, request.user, body)

        msg = (
            Message.objects.filter(pk=message.pk)
            .select_related('author', 'reply_to', 'reply_to__author')
            .prefetch_related(
                Prefetch(
                    'reactions',
                    queryset=Reaction.objects.select_related('user'),
                ),
                'attachments',
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
        membership = _get_active_membership(request.user, conversation_id)
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
        message.body_html = render_message_body(message.body)
        message.edited_at = timezone.now()
        message.save(update_fields=['body', 'body_html', 'edited_at'])

        notify_conversation_members(
            message.conversation, exclude_user=request.user,
        )

        # Refetch with prefetches for serialization
        message = (
            Message.objects.filter(pk=message.pk)
            .select_related('author', 'reply_to', 'reply_to__author')
            .prefetch_related(
                Prefetch(
                    'reactions',
                    queryset=Reaction.objects.select_related('user'),
                ),
                'attachments',
            )
            .first()
        )
        return Response(MessageSerializer(message).data)

    @extend_schema(summary="Delete a message (soft)")
    def delete(self, request, conversation_id, message_id):
        membership = _get_active_membership(request.user, conversation_id)
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
            )
        except Message.DoesNotExist:
            return Response(
                {'detail': 'Message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if message.author_id != request.user.id:
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
            )
        except Message.DoesNotExist:
            return Response(
                {'detail': 'Message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = _get_active_membership(request.user, message.conversation_id)
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


class MemberAddSerializer(serializers.Serializer):
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
    )


@extend_schema(tags=['Chat'])
class ConversationMembersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Add members to a group conversation",
        request=MemberAddSerializer,
    )
    def post(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Can only add members to group conversations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = MemberAddSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user_ids = ser.validated_data['user_ids']

        users = User.objects.filter(id__in=user_ids)
        if users.count() != len(user_ids):
            return Response(
                {'detail': 'One or more user IDs are invalid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Batch-fetch existing memberships (1 query instead of N)
        existing_members = {
            m.user_id: m
            for m in ConversationMember.objects.filter(
                conversation=conversation, user__in=users,
            )
        }

        added = []
        to_create = []
        for u in users:
            existing = existing_members.get(u.id)
            if existing:
                if existing.left_at is not None:
                    existing.left_at = None
                    existing.unread_count = 0
                    existing.save(update_fields=['left_at', 'unread_count'])
                    added.append(u.id)
                # Already active member — skip silently
            else:
                to_create.append(
                    ConversationMember(conversation=conversation, user=u)
                )
                added.append(u.id)

        if to_create:
            ConversationMember.objects.bulk_create(to_create)

        # Refetch conversation with members
        conversation = (
            Conversation.objects.filter(pk=conversation.pk)
            .prefetch_related(
                Prefetch(
                    'members',
                    queryset=ConversationMember.objects.filter(
                        left_at__isnull=True,
                    ).select_related('user'),
                ),
            )
            .first()
        )
        return Response(
            ConversationDetailSerializer(conversation).data,
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=['Chat'])
class ConversationMemberRemoveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove a member from a group conversation (creator only)")
    def delete(self, request, conversation_id, user_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Can only remove members from group conversations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation.created_by_id != request.user.id:
            return Response(
                {'detail': 'Only the group creator can remove members.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if user_id == request.user.id:
            return Response(
                {'detail': 'Cannot remove yourself. Use leave instead.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_membership = ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not target_membership:
            return Response(
                {'detail': 'User is not an active member.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        target_membership.left_at = timezone.now()
        target_membership.save(update_fields=['left_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat'])
class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark conversation as read")
    def post(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        membership.last_read_at = timezone.now()
        membership.unread_count = 0
        membership.save(update_fields=['last_read_at', 'unread_count'])

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


@extend_schema(tags=['Chat'])
class MessageReadersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get who has read a specific message")
    def get(self, request, conversation_id, message_id):
        membership = _get_active_membership(request.user, conversation_id)
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
class UnreadCountsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get unread message counts")
    def get(self, request):
        return Response(get_unread_counts(request.user))


AVATAR_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@extend_schema(tags=['Chat'])
class GroupAvatarUploadView(APIView):
    """Upload or delete a group conversation's avatar."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    @extend_schema(
        summary="Upload group avatar",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "format": "binary"},
                    "crop_x": {"type": "number"},
                    "crop_y": {"type": "number"},
                    "crop_w": {"type": "number"},
                    "crop_h": {"type": "number"},
                },
                "required": ["image", "crop_x", "crop_y", "crop_w", "crop_h"],
            }
        },
        responses={
            200: inline_serializer(
                name="GroupAvatarUploadResponse",
                fields={"message": serializers.CharField()},
            ),
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Forbidden"),
        },
    )
    def post(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Avatars are only supported for group conversations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image = request.FILES.get("image")
        if not image:
            return Response(
                {"errors": ["No image provided."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image.content_type not in AVATAR_ALLOWED_TYPES:
            return Response(
                {"errors": ["Unsupported image type. Use JPEG, PNG, WebP, or GIF."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image.size > AVATAR_MAX_SIZE:
            return Response(
                {"errors": ["Image too large. Maximum size is 10 MB."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            crop_x = float(request.data.get("crop_x", 0))
            crop_y = float(request.data.get("crop_y", 0))
            crop_w = float(request.data.get("crop_w", 0))
            crop_h = float(request.data.get("crop_h", 0))
        except (TypeError, ValueError):
            return Response(
                {"errors": ["Invalid crop coordinates."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if crop_w <= 0 or crop_h <= 0:
            return Response(
                {"errors": ["Crop width and height must be positive."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group_avatar_service.process_and_save_group_avatar(
            conversation, image, crop_x, crop_y, crop_w, crop_h,
        )
        return Response({"message": "Group avatar updated successfully."})

    @extend_schema(
        summary="Delete group avatar",
        responses={
            200: inline_serializer(
                name="GroupAvatarDeleteResponse",
                fields={"message": serializers.CharField()},
            ),
            403: OpenApiResponse(description="Forbidden"),
        },
    )
    def delete(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Avatars are only supported for group conversations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group_avatar_service.delete_group_avatar(conversation)
        return Response({"message": "Group avatar removed."})


@extend_schema(tags=['Chat'])
class GroupAvatarRetrieveView(APIView):
    """Serve a group conversation's avatar image (public)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Get group avatar",
        responses={
            200: OpenApiResponse(description="Avatar image (WebP)"),
            304: OpenApiResponse(description="Not modified"),
            404: OpenApiResponse(description="No avatar found"),
        },
    )
    def get(self, request, conversation_id):
        path = group_avatar_service.get_group_avatar_path(conversation_id)
        if not default_storage.exists(path):
            return HttpResponse(status=404)

        etag = group_avatar_service.get_group_avatar_etag(conversation_id)
        if etag:
            if_none_match = request.META.get("HTTP_IF_NONE_MATCH")
            if if_none_match and if_none_match.strip('"') == etag:
                response = HttpResponse(status=304)
                response["ETag"] = f'"{etag}"'
                return response

        avatar_file = default_storage.open(path, "rb")
        response = FileResponse(avatar_file, content_type="image/webp")
        response["Cache-Control"] = "no-cache"
        if etag:
            response["ETag"] = f'"{etag}"'
        return response


@extend_schema(tags=['Chat'])
class ConversationStatsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get conversation statistics")
    def get(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        active_messages = Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )

        aggregates = active_messages.aggregate(
            message_count=Count('uuid'),
            first_message_at=Min('created_at'),
            last_message_at=Max('created_at'),
        )

        reaction_count = Reaction.objects.filter(
            message__conversation_id=conversation_id,
            message__deleted_at__isnull=True,
        ).count()

        messages_per_member = list(
            active_messages
            .values(username=F('author__username'))
            .annotate(count=Count('uuid'))
            .order_by('-count')
        )

        return Response({
            'message_count': aggregates['message_count'],
            'reaction_count': reaction_count,
            'first_message_at': aggregates['first_message_at'],
            'last_message_at': aggregates['last_message_at'],
            'messages_per_member': messages_per_member,
        })


@extend_schema(tags=['Chat'])
class ConversationMessageSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search messages in a conversation",
        parameters=[
            OpenApiParameter(name='q', type=str, required=False),
            OpenApiParameter(name='author', type=int, required=False),
            OpenApiParameter(name='date_range', type=str, required=False, enum=['today', '7d', '30d']),
            OpenApiParameter(name='date_from', type=str, required=False, description='ISO date (YYYY-MM-DD)'),
            OpenApiParameter(name='date_to', type=str, required=False, description='ISO date (YYYY-MM-DD)'),
            OpenApiParameter(name='has_files', type=bool, required=False),
            OpenApiParameter(name='has_images', type=bool, required=False),
        ],
    )
    def get(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        params = request.query_params
        query = params.get('q', '').strip()
        author_id = params.get('author', '').strip()
        date_range = params.get('date_range', '').strip()
        date_from = params.get('date_from', '').strip()
        date_to = params.get('date_to', '').strip()
        has_files = params.get('has_files', '').lower() == 'true'
        has_images = params.get('has_images', '').lower() == 'true'

        has_any = query or author_id or date_range or date_from or date_to or has_files or has_images
        if not has_any:
            return Response(
                {'detail': 'At least one search criterion is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )

        if query:
            qs = qs.filter(body__icontains=query)

        if author_id:
            qs = qs.filter(author_id=author_id)

        now = timezone.now()
        if date_range == 'today':
            qs = qs.filter(created_at__date=now.date())
        elif date_range == '7d':
            qs = qs.filter(created_at__gte=now - timedelta(days=7))
        elif date_range == '30d':
            qs = qs.filter(created_at__gte=now - timedelta(days=30))

        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        if has_files:
            qs = qs.filter(attachments__isnull=False)
        if has_images:
            qs = qs.filter(attachments__mime_type__startswith='image/')

        messages = (
            qs.select_related('author')
            .order_by('-created_at')
            .distinct()[:50]
        )

        results = [
            {
                'uuid': str(msg.uuid),
                'author': {
                    'id': msg.author.id,
                    'username': msg.author.username,
                },
                'body': msg.body,
                'body_html': msg.body_html,
                'created_at': msg.created_at.isoformat(),
            }
            for msg in messages
        ]

        return Response({
            'results': results,
            'query': query,
            'count': len(results),
        })


@extend_schema(tags=['Chat'])
class ConversationPinView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Pin a conversation")
    def post(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if PinnedConversation.objects.filter(
            owner=request.user, conversation_id=conversation_id,
        ).exists():
            return Response({'detail': 'Already pinned.'}, status=status.HTTP_200_OK)

        max_pos = PinnedConversation.objects.filter(
            owner=request.user,
        ).aggregate(max_pos=Max('position'))['max_pos']
        next_pos = (max_pos or 0) + 1

        PinnedConversation.objects.create(
            owner=request.user,
            conversation_id=conversation_id,
            position=next_pos,
        )
        return Response({'status': 'pinned'}, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Unpin a conversation")
    def delete(self, request, conversation_id):
        deleted, _ = PinnedConversation.objects.filter(
            owner=request.user, conversation_id=conversation_id,
        ).delete()
        if not deleted:
            return Response(
                {'detail': 'Not pinned.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat'])
class ConversationPinReorderView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Reorder pinned conversations")
    def post(self, request):
        order = request.data.get('order', [])
        if not isinstance(order, list):
            return Response(
                {'detail': '"order" must be a list of conversation UUIDs.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pins = PinnedConversation.objects.filter(owner=request.user)
        pin_map = {str(p.conversation_id): p for p in pins}

        to_update = []
        for i, uuid_str in enumerate(order):
            pin = pin_map.get(uuid_str)
            if pin:
                pin.position = i
                to_update.append(pin)

        if to_update:
            PinnedConversation.objects.bulk_update(to_update, ['position'])

        return Response({'status': 'ok'})


@extend_schema(tags=['Chat'])
class MessagePinToggleView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Pin a message")
    def post(self, request, message_id):
        try:
            message = Message.objects.select_related('conversation').get(uuid=message_id)
        except Message.DoesNotExist:
            return Response({'detail': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)

        membership = _get_active_membership(request.user, message.conversation_id)
        if not membership:
            return Response({'detail': 'Not a member of this conversation.'}, status=status.HTTP_403_FORBIDDEN)

        if message.deleted_at:
            return Response({'detail': 'Cannot pin a deleted message.'}, status=status.HTTP_400_BAD_REQUEST)

        pin, created = PinnedMessage.objects.get_or_create(
            conversation=message.conversation,
            message=message,
            defaults={'pinned_by': request.user},
        )

        notify_conversation_members(message.conversation, exclude_user=request.user)

        pin = PinnedMessage.objects.select_related('message__author', 'pinned_by').get(pk=pin.pk)
        return Response(
            PinnedMessageSerializer(pin).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(summary="Unpin a message")
    def delete(self, request, message_id):
        try:
            message = Message.objects.select_related('conversation').get(uuid=message_id)
        except Message.DoesNotExist:
            return Response({'detail': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)

        membership = _get_active_membership(request.user, message.conversation_id)
        if not membership:
            return Response({'detail': 'Not a member of this conversation.'}, status=status.HTTP_403_FORBIDDEN)

        deleted, _ = PinnedMessage.objects.filter(
            conversation=message.conversation,
            message=message,
        ).delete()
        if not deleted:
            return Response({'detail': 'Message is not pinned.'}, status=status.HTTP_404_NOT_FOUND)

        notify_conversation_members(message.conversation, exclude_user=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat'])
class ConversationPinnedMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List pinned messages in a conversation")
    def get(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response({'detail': 'Not a member of this conversation.'}, status=status.HTTP_403_FORBIDDEN)

        pins = (
            PinnedMessage.objects
            .filter(conversation_id=conversation_id)
            .select_related('message__author', 'pinned_by')
            .order_by('-created_at')
        )
        return Response(PinnedMessageSerializer(pins, many=True).data)


@extend_schema(tags=['Chat'])
class AttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Download a chat attachment")
    def get(self, request, attachment_id):
        try:
            attachment = (
                MessageAttachment.objects
                .select_related('message')
                .get(uuid=attachment_id)
            )
        except MessageAttachment.DoesNotExist:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = _get_active_membership(
            request.user, attachment.message.conversation_id,
        )
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        response = FileResponse(
            attachment.file.open('rb'),
            content_type=attachment.mime_type,
        )
        # Sanitize filename for Content-Disposition header
        safe_name = attachment.original_name.replace('"', '\\"').replace('\n', '').replace('\r', '')
        response['Content-Disposition'] = f'inline; filename="{safe_name}"'
        return response


@extend_schema(tags=['Chat'])
class AttachmentSaveToFilesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Save a chat attachment to the user's Files")
    def post(self, request, attachment_id):
        from django.core.files.base import ContentFile
        from workspace.files.services.files import FileService

        try:
            attachment = (
                MessageAttachment.objects
                .select_related('message')
                .get(uuid=attachment_id)
            )
        except MessageAttachment.DoesNotExist:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = _get_active_membership(
            request.user, attachment.message.conversation_id,
        )
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from workspace.files.models import File

        parent = None
        folder_id = request.data.get('folder_id')
        if folder_id:
            try:
                parent = File.objects.get(
                    uuid=folder_id,
                    owner=request.user,
                    node_type=File.NodeType.FOLDER,
                    deleted_at__isnull=True,
                )
            except File.DoesNotExist:
                return Response(
                    {'detail': 'Folder not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        content = ContentFile(attachment.file.read(), name=attachment.original_name)
        file_obj = FileService.create_file(
            owner=request.user,
            name=attachment.original_name,
            parent=parent,
            content=content,
            mime_type=attachment.mime_type,
        )

        return Response(
            {'detail': 'File saved.', 'file_uuid': str(file_obj.uuid)},
            status=status.HTTP_201_CREATED,
        )

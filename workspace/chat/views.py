from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db.models import Count, F, Max, Min, OuterRef, Prefetch, Q, Subquery
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, OpenApiResponse, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import avatar_service as group_avatar_service
from .models import Conversation, ConversationMember, Message, Reaction
from .serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    MessageCreateSerializer,
    MessageEditSerializer,
    MessageSerializer,
    ReactionToggleSerializer,
)
from .services import (
    get_or_create_dm,
    get_unread_counts,
    notify_conversation_members,
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
            for m in Message.objects.filter(uuid__in=last_msg_ids).select_related('author')
        }

        for c in conv_list:
            c._last_message = last_msgs.get(c._last_msg_id)
            c.unread_count = unread_map.get(str(c.uuid), 0)

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

    @extend_schema(summary="Rename group conversation")
    def patch(self, request, conversation_id):
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Only group conversations can be renamed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = request.data.get('title', '').strip()
        if not title:
            return Response(
                {'detail': 'Title is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        conversation.title = title
        conversation.save(update_fields=['title'])
        return Response({'uuid': str(conversation.uuid), 'title': conversation.title})

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
        ).select_related('author').prefetch_related(
            Prefetch(
                'reactions',
                queryset=Reaction.objects.select_related('user'),
            ),
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
        membership = _get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        body = serializer.validated_data['body']
        body_html = render_message_body(body)

        message = Message.objects.create(
            conversation_id=conversation_id,
            author=request.user,
            body=body,
            body_html=body_html,
        )

        # Bump conversation updated_at
        Conversation.objects.filter(pk=conversation_id).update(
            updated_at=timezone.now(),
        )

        # Notify other members via SSE cache
        notify_conversation_members(
            message.conversation, exclude_user=request.user,
        )

        msg = (
            Message.objects.filter(pk=message.pk)
            .select_related('author')
            .prefetch_related(
                Prefetch(
                    'reactions',
                    queryset=Reaction.objects.select_related('user'),
                ),
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
            message = Message.objects.get(
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
            message = Message.objects.get(
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

        added = []
        for u in users:
            # Re-activate if they previously left, otherwise create
            existing = ConversationMember.objects.filter(
                conversation=conversation, user=u,
            ).first()
            if existing:
                if existing.left_at is not None:
                    existing.left_at = None
                    existing.save(update_fields=['left_at'])
                    added.append(u.id)
                # Already active member — skip silently
            else:
                ConversationMember.objects.create(
                    conversation=conversation, user=u,
                )
                added.append(u.id)

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
        membership.save(update_fields=['last_read_at'])
        return Response({'status': 'ok'})


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
        response["Cache-Control"] = "public, max-age=3600"
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

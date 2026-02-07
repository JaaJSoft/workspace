from django.contrib.auth import get_user_model
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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

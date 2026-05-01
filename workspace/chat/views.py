import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import OuterRef, Prefetch, Subquery
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.logging import scrub
from workspace.common.mixins import CacheControlMixin
from .models import Conversation, ConversationMember, Message, PinnedConversation
from .serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
)
from .services.conversations import get_active_membership, get_or_create_dm, get_unread_counts, user_conversation_ids

User = get_user_model()
logger = logging.getLogger(__name__)


def _trigger_bot_response(conversation_id, message, sender):
    """If the conversation includes a bot, trigger an AI response."""
    bot_member = (
        ConversationMember.objects.filter(
            conversation_id=conversation_id,
            left_at__isnull=True,
            user__bot_profile__isnull=False,
        )
        .exclude(user=sender)
        .select_related('user')
        .first()
    )
    if bot_member:
        try:
            from workspace.ai.tasks import generate_chat_response
            generate_chat_response.delay(
                str(conversation_id),
                str(message.uuid),
                bot_member.user_id,
            )
        except Exception:
            logger.exception('Failed to trigger bot response for conversation=%s', scrub(conversation_id))


@extend_schema(tags=['Chat'])
class ConversationListView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List active conversations")
    def get(self, request):
        user = request.user

        # Get conversation IDs where user is an active member
        member_convos = user_conversation_ids(user)

        conversations = (
            Conversation.objects.filter(uuid__in=member_convos)
            .prefetch_related(
                Prefetch(
                    'members',
                    queryset=ConversationMember.objects.filter(
                        left_at__isnull=True,
                    ).select_related('user', 'user__bot_profile'),
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
    @transaction.atomic
    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        member_ids = serializer.validated_data['member_ids']
        title = serializer.validated_data.get('title', '')

        # Validate that all member_ids exist and are active
        users = User.objects.filter(id__in=member_ids, is_active=True).select_related('bot_profile')
        if users.count() != len(member_ids):
            return Response(
                {'detail': 'One or more user IDs are invalid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check bot access permissions
        bot_users = users.filter(bot_profile__isnull=False).select_related('bot_profile')
        for bot_user in bot_users:
            if not bot_user.bot_profile.is_accessible_by(request.user):
                return Response(
                    {'detail': 'You do not have access to this bot.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # `created_members` is populated on the fresh-creation paths so the
        # response can serialize without a post-INSERT refetch. On the
        # get_or_create_dm path we may be returning a pre-existing DM whose
        # live member state we don't hold in memory - keep the refetch there.
        created_members = None

        if len(member_ids) == 1:
            other_user = users.first()
            if other_user.id == request.user.id:
                return Response(
                    {'detail': 'Cannot create a DM with yourself.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if hasattr(other_user, 'bot_profile'):
                conversation = Conversation.objects.create(
                    kind=Conversation.Kind.DM,
                    created_by=request.user,
                )
                created_members = [
                    ConversationMember(conversation=conversation, user=request.user),
                    ConversationMember(conversation=conversation, user=other_user),
                ]
                ConversationMember.objects.bulk_create(created_members)
            else:
                conversation = get_or_create_dm(request.user, other_user)
        else:
            conversation = Conversation.objects.create(
                kind=Conversation.Kind.GROUP,
                title=title,
                created_by=request.user,
            )
            created_members = [
                ConversationMember(conversation=conversation, user=request.user),
            ]
            for u in users:
                if u.id != request.user.id:
                    created_members.append(
                        ConversationMember(conversation=conversation, user=u),
                    )
            ConversationMember.objects.bulk_create(created_members)

        if created_members is not None:
            conversation._prefetched_objects_cache = {'members': created_members}
        else:
            conversation = (
                Conversation.objects.filter(pk=conversation.pk)
                .prefetch_related(
                    Prefetch(
                        'members',
                        queryset=ConversationMember.objects.filter(
                            left_at__isnull=True,
                        ).select_related('user', 'user__bot_profile'),
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
        membership = get_active_membership(request.user, conversation_id)
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
                    ).select_related('user', 'user__bot_profile'),
                ),
            )
            .first()
        )
        return Response(ConversationDetailSerializer(conversation).data)

    @extend_schema(
        summary="Update conversation details",
        request=inline_serializer('ConversationUpdate', fields={
            'title': serializers.CharField(required=False),
            'description': serializers.CharField(required=False),
        }),
    )
    def patch(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        update_fields = []

        # Title update (groups and bot conversations)
        if 'title' in request.data:
            is_bot_conv = conversation.members.filter(
                user__bot_profile__isnull=False, left_at__isnull=True,
            ).exists()
            if conversation.kind != Conversation.Kind.GROUP and not is_bot_conv:
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
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        membership.left_at = timezone.now()
        membership.save(update_fields=['left_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


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
    @transaction.atomic
    def post(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
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

        users = User.objects.filter(id__in=user_ids, is_active=True)
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
                # Already active member - skip silently
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
                    ).select_related('user', 'user__bot_profile'),
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
        membership = get_active_membership(request.user, conversation_id)
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

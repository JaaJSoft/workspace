import logging
import uuid

from django.db.models import Max
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Message, PinnedConversation, PinnedMessage
from .serializers import PinnedMessageSerializer
from .services.conversations import get_active_membership
from .services.notifications import notify_conversation_members

logger = logging.getLogger(__name__)


@extend_schema(tags=['Chat'])
class ConversationPinView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Pin a conversation")
    def post(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        max_pos = PinnedConversation.objects.filter(
            owner=request.user,
        ).aggregate(max_pos=Max('position'))['max_pos']
        next_pos = (max_pos or 0) + 1

        # get_or_create avoids the exists()+create() race that would otherwise
        # 500 on the unique_user_pinned_conversation constraint when a client
        # double-submits.
        _, created = PinnedConversation.objects.get_or_create(
            owner=request.user,
            conversation_id=conversation_id,
            defaults={'position': next_pos},
        )
        if not created:
            return Response({'detail': 'Already pinned.'}, status=status.HTTP_200_OK)
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

    @extend_schema(
        summary="Reorder pinned conversations",
        request=inline_serializer('ConversationPinReorder', fields={
            'order': serializers.ListField(child=serializers.UUIDField()),
        }),
    )
    def post(self, request):
        order = request.data.get('order', [])
        if not isinstance(order, list):
            return Response(
                {'detail': '"order" must be a list of conversation UUIDs.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Normalize entries up front. Unhashable JSON shapes (list/dict) would
        # otherwise crash at pin_map.get(uuid_str) with TypeError. Strings that
        # don't parse as UUIDs are kept as-is and silently miss the lookup,
        # matching the existing behaviour for unknown conversation IDs.
        normalized_order = []
        for entry in order:
            if isinstance(entry, uuid.UUID):
                normalized_order.append(str(entry))
            elif isinstance(entry, str):
                normalized_order.append(entry)
            else:
                return Response(
                    {'detail': '"order" entries must be UUID strings.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        pins = PinnedConversation.objects.filter(owner=request.user)
        pin_map = {str(p.conversation_id): p for p in pins}

        to_update = []
        for i, uuid_str in enumerate(normalized_order):
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

        membership = get_active_membership(request.user, message.conversation_id)
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

        membership = get_active_membership(request.user, message.conversation_id)
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
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response({'detail': 'Not a member of this conversation.'}, status=status.HTTP_403_FORBIDDEN)

        pins = (
            PinnedMessage.objects
            .filter(conversation_id=conversation_id)
            .select_related('message__author', 'pinned_by')
            .order_by('-created_at')
        )
        return Response(PinnedMessageSerializer(pins, many=True).data)

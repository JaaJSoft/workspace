import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..common.logging import scrub
from .models import Conversation, ConversationMember, Message, MessageAttachment
from .services.conversations import get_active_membership, get_unread_counts
from .services.notifications import notify_conversation_members

logger = logging.getLogger(__name__)


@extend_schema(tags=["Chat"])
class TypingIndicatorView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Signal typing", request=None, responses={200: None})
    def post(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .services.typing import set_typing

        set_typing(
            conversation_id,
            request.user.id,
            request.user.get_full_name() or request.user.username,
        )
        notify_conversation_members(
            Conversation(pk=conversation_id),
            exclude_user=request.user,
        )
        return Response({"status": "ok"})


@extend_schema(tags=["Chat"])
class UnreadCountsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get unread message counts")
    def get(self, request):
        return Response(get_unread_counts(request.user))


@extend_schema(tags=["Chat"])
class ConversationClearView(APIView):
    """DELETE /api/v1/chat/conversations/<id>/messages - Clear all messages and attachments."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Chat"], summary="Clear all messages in a conversation")
    def delete(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(status=status.HTTP_404_NOT_FOUND)

        messages = Message.objects.filter(conversation_id=conversation_id)

        # Snapshot attachment file references before the delete so we can
        # remove them from storage AFTER the DB transaction commits. Otherwise
        # a rollback would leave attachment rows pointing at missing blobs.
        attachment_files = [
            att.file
            for att in MessageAttachment.objects.filter(message__in=messages).iterator()
            if att.file
        ]

        # Delete all messages (hard delete, not soft) + reset unread counts
        with transaction.atomic():
            count, _ = messages.delete()
            ConversationMember.objects.filter(
                conversation_id=conversation_id,
                left_at__isnull=True,
            ).update(unread_count=0)

            def _cleanup_files():
                for f in attachment_files:
                    try:
                        f.delete(save=False)
                    except OSError:
                        logger.warning("Could not delete file %s", scrub(f.name))

            transaction.on_commit(_cleanup_files)

        notify_conversation_members(
            Conversation.objects.get(pk=conversation_id),
            exclude_user=request.user,
        )

        return Response({"deleted": count}, status=status.HTTP_200_OK)

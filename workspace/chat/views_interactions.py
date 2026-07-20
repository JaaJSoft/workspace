import logging

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Message, MessageInteraction, Reaction
from .serializers import MessageSerializer
from .services.conversations import get_active_membership
from .services.rendering import render_message_body
from .views import _trigger_bot_response

logger = logging.getLogger(__name__)


def _refetch_for_serialization(message_pk):
    return (
        Message.objects.filter(pk=message_pk)
        .select_related(
            "author",
            "author__bot_profile",
            "reply_to",
            "reply_to__author",
            "interaction",
            "interaction__interacted_by",
        )
        .prefetch_related(
            Prefetch("reactions", queryset=Reaction.objects.select_related("user")),
            "attachments",
            "link_previews__preview",
        )
        .first()
    )


@extend_schema(tags=["Chat"])
class MessageInteractionAnswerView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Answer an AI question by clicking a suggested option")
    def post(self, request, message_id):
        raw = request.data.get("option_index")
        if raw is None:
            return Response(
                {"detail": "option_index is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            option_index = int(raw)
        except (TypeError, ValueError):
            return Response(
                {"detail": "option_index must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        interaction = (
            MessageInteraction.objects.select_related("message__conversation")
            .filter(
                message_id=message_id,
                kind=MessageInteraction.Kind.QUESTION,
            )
            .first()
        )
        if interaction is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        membership = get_active_membership(
            request.user,
            interaction.message.conversation_id,
        )
        if not membership:
            return Response(status=status.HTTP_404_NOT_FOUND)

        options = (interaction.payload or {}).get("options", [])
        if not (0 <= option_index < len(options)):
            return Response(
                {"detail": "option_index out of range"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            locked = MessageInteraction.objects.select_for_update().get(
                uuid=interaction.uuid
            )
            if locked.interacted_at is not None:
                same_user = locked.interacted_by_id == request.user.id
                same_choice = (locked.state or {}).get("selected_index") == option_index
                if same_user and same_choice:
                    answer = _refetch_for_serialization(
                        locked.state["answer_message_id"],
                    )
                    return Response(
                        MessageSerializer(answer).data,
                        status=status.HTTP_200_OK,
                    )
                return Response(
                    {"detail": "already answered"},
                    status=status.HTTP_409_CONFLICT,
                )

            answer_body = options[option_index]
            answer = Message.objects.create(
                conversation_id=interaction.message.conversation_id,
                author=request.user,
                body=answer_body,
                body_html=render_message_body(answer_body),
                reply_to=interaction.message,
            )
            locked.interacted_at = timezone.now()
            locked.interacted_by = request.user
            locked.state = {
                "selected_index": option_index,
                "answer_message_id": str(answer.uuid),
            }
            locked.save(
                update_fields=["interacted_at", "interacted_by", "state"],
            )

        _trigger_bot_response(
            interaction.message.conversation_id,
            answer,
            request.user,
        )

        answer = _refetch_for_serialization(answer.pk)
        return Response(
            MessageSerializer(answer).data,
            status=status.HTTP_201_CREATED,
        )

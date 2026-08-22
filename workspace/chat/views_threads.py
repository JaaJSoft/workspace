from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ConversationMember, Message
from .services.conversations import get_active_membership
from .services.threads import mark_thread_read


class ThreadReadSerializer(serializers.Serializer):
    cleared = serializers.IntegerField()


@extend_schema(tags=["Chat - Messages"])
class ThreadReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Mark a thread as read",
        request=None,
        responses={200: OpenApiResponse(response=ThreadReadSerializer)},
    )
    def post(self, request, root_uuid):
        root = get_object_or_404(Message, uuid=root_uuid, thread_root__isnull=True)
        if not get_active_membership(request.user, root.conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        # One transaction: the thread counter and the conversation badge are two
        # halves of the same number, so a crash between them would leave the
        # badge counting replies the thread no longer has.
        with transaction.atomic():
            cleared = mark_thread_read(root, request.user)
            if cleared:
                # Greatest, not a plain subtraction: the conversation badge is
                # denormalised and must never be pushed below zero by a thread
                # whose counter drifted.
                ConversationMember.objects.filter(
                    conversation_id=root.conversation_id,
                    user=request.user,
                ).update(
                    unread_count=Greatest(F("unread_count") - cleared, Value(0)),
                )
        return Response({"cleared": cleared})

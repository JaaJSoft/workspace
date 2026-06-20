import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import calls
from .services.call_signaling import send_signal
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@extend_schema(tags=["Chat"])
class CallStateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Current call state for a conversation")
    def get(self, request, conversation_id):
        if not get_active_membership(request.user, conversation_id):
            return Response(status=status.HTTP_404_NOT_FOUND)
        session = calls.get_active_call(conversation_id)
        if session is None:
            return Response({"active": False})
        return Response(calls.serialize_call_state(session))


@extend_schema(tags=["Chat"])
class CallJoinView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Join or start the conversation call", request=None)
    def post(self, request, conversation_id):
        if not get_active_membership(request.user, conversation_id):
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            session, _, _ = calls.start_or_join_call(request.user, conversation_id)
        except calls.CallFull:
            return Response(
                {"detail": "Call is full."}, status=status.HTTP_409_CONFLICT
            )
        return Response(
            {
                "state": calls.serialize_call_state(session),
                "ice_servers": getattr(settings, "CHAT_CALL_ICE_SERVERS", []),
            }
        )


@extend_schema(tags=["Chat"])
class CallLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Leave the conversation call", request=None)
    def post(self, request, conversation_id):
        # No membership gate beyond auth: a user who just left the conversation
        # must still be able to drop out of a call cleanly. leave_call is a
        # no-op when there is no active call.
        calls.leave_call(request.user, conversation_id)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat"])
class CallSignalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Relay a WebRTC signal to a peer")
    def post(self, request, conversation_id):
        if not get_active_membership(request.user, conversation_id):
            return Response(status=status.HTTP_404_NOT_FOUND)

        to_user_id = request.data.get("to_user_id")
        signal = request.data.get("signal")
        if (
            isinstance(to_user_id, bool)
            or not isinstance(to_user_id, int)
            or not isinstance(signal, dict)
        ):
            return Response(
                {"detail": "to_user_id (int) and signal (object) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # The target must be an active member of this same conversation.
        if not get_active_membership_by_id(to_user_id, conversation_id):
            return Response(
                {"detail": "Target is not a member."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        send_signal(conversation_id, to_user_id, request.user.id, signal)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat"])
class CallHeartbeatView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Refresh call presence and media state")
    def post(self, request, conversation_id):
        if not get_active_membership(request.user, conversation_id):
            return Response(status=status.HTTP_404_NOT_FOUND)
        session = calls.get_active_call(conversation_id)
        if session is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        media_state = request.data.get("media_state")
        if not isinstance(media_state, dict):
            media_state = dict(calls.DEFAULT_MEDIA_STATE)

        changed = calls.touch_presence(session.uuid, request.user.id, media_state)
        if changed:
            calls._broadcast(
                conversation_id,
                "call_participant_updated",
                {
                    "session_id": str(session.uuid),
                    "user_id": request.user.id,
                    "media_state": media_state,
                },
                exclude_user_id=request.user.id,
            )
        return Response({"status": "ok"})


def get_active_membership_by_id(user_id, conversation_id):
    """Active-membership check for an arbitrary user id (target of a signal)."""
    from .models import ConversationMember

    return ConversationMember.objects.filter(
        conversation_id=conversation_id, user_id=user_id, left_at__isnull=True
    ).exists()

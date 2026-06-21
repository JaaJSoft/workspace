import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import calls
from .services.call_signaling import send_diagnostic_signal, send_signal
from .services.conversations import (
    get_active_membership,
    is_active_member,
    is_bot_conversation,
)

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
        if is_bot_conversation(conversation_id):
            return Response(
                {"detail": "Calls are not available in AI conversations."},
                status=status.HTTP_400_BAD_REQUEST,
            )
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
        if not is_active_member(to_user_id, conversation_id):
            return Response(
                {"detail": "Target is not a member."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Signals are scoped to the active call session, so the envelope must
        # carry the session id (not the conversation id) for client-side
        # session filtering. No active call means there is nothing to signal.
        session = calls.get_active_call(conversation_id)
        if session is None:
            return Response(
                {"detail": "No active call."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        send_signal(session.uuid, to_user_id, request.user.id, signal)
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


@extend_schema(tags=["Chat"])
class CallDiagnosticSignalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Echo a diagnostic WebRTC signal back to the sender")
    def post(self, request):
        lane = request.data.get("lane")
        signal = request.data.get("signal")
        run_id = request.data.get("run_id")
        valid_lanes = ("to_caller", "to_callee")
        if (
            lane not in valid_lanes
            or not isinstance(signal, dict)
            or not isinstance(run_id, str)
            or not run_id
        ):
            return Response(
                {
                    "detail": (
                        "lane (to_caller|to_callee), signal (object) and "
                        "run_id (string) are required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        send_diagnostic_signal(request.user.id, lane, signal, run_id)
        return Response({"status": "ok"})

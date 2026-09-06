import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import CallParticipant
from ..services import calls
from ..services.call_signaling import (
    DIAGNOSTIC_LANES,
    send_diagnostic_signal,
    send_signal,
)
from ..services.conversations import (
    get_active_membership,
    is_active_member,
    is_bot_conversation,
)
from ..services.participant_keys import (
    guest_key,
    guest_uuid_from_key,
    user_id_from_key,
    user_key,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Chat - Calls"])
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


@extend_schema(tags=["Chat - Calls"])
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


@extend_schema(tags=["Chat - Calls"])
class CallLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Leave the conversation call", request=None)
    def post(self, request, conversation_id):
        # No membership gate beyond auth: a user who just left the conversation
        # must still be able to drop out of a call cleanly. leave_call is a
        # no-op when there is no active call.
        calls.leave_call(request.user, conversation_id)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Calls"])
class CallSignalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Relay a WebRTC signal to a peer")
    def post(self, request, conversation_id):
        if not get_active_membership(request.user, conversation_id):
            return Response(status=status.HTTP_404_NOT_FOUND)

        to_participant = request.data.get("to_participant")
        signal = request.data.get("signal")
        if not isinstance(to_participant, str) or not isinstance(signal, dict):
            return Response(
                {"detail": "to_participant (string) and signal (object) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Signals are scoped to the active call session, so the envelope must
        # carry the session id (not the conversation id) for client-side
        # session filtering. No active call means there is nothing to signal -
        # and a guest target can only be resolved against a session anyway,
        # so this check has to run before target resolution now.
        session = calls.get_active_call(conversation_id)
        if session is None:
            return Response(
                {"detail": "No active call."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # A member may reach another active member of this conversation, or a
        # meeting guest who is themselves an active participant of this SAME
        # session - the guest branch mirrors MeetingGuestSignalView's own.
        # The member branch stays looser on purpose (active member, not
        # active CallParticipant): a member may signal a fellow member who
        # has not joined the call yet, which is how a ringing invite works.
        # A guest has no such use case - a guest reaches the call surface
        # only by joining it - so the guest branch requires participation.
        target_user_id = user_id_from_key(to_participant)
        target_guest_uuid = guest_uuid_from_key(to_participant)
        if target_user_id is not None:
            target_key = user_key(target_user_id)
            target_ok = is_active_member(target_user_id, conversation_id)
        elif target_guest_uuid is not None:
            target_key = guest_key(target_guest_uuid)
            target_ok = CallParticipant.objects.filter(
                session=session, guest_id=target_guest_uuid, left_at__isnull=True
            ).exists()
        else:
            target_key = None
            target_ok = False

        if not target_ok:
            return Response(
                {"detail": "Target is not a member."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        send_signal(session.uuid, target_key, user_key(request.user.id), signal)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Calls"])
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

        key = user_key(request.user.id)
        changed = calls.touch_presence(session.uuid, key, media_state)
        if changed:
            calls._broadcast(
                conversation_id,
                "call_participant_updated",
                {
                    "session_id": str(session.uuid),
                    "participant_key": key,
                    "media_state": media_state,
                },
                exclude_key=key,
            )
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Calls"])
class CallDiagnosticSignalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Echo a diagnostic WebRTC signal back to the sender")
    def post(self, request):
        lane = request.data.get("lane")
        signal = request.data.get("signal")
        run_id = request.data.get("run_id")
        if (
            lane not in DIAGNOSTIC_LANES
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

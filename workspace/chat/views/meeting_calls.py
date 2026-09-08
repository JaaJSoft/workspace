"""The hosts' side of a meeting call, mirroring views/calls.py on a meeting
scope. Guests reach the same call through views/meeting_guest.py with a token."""

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import CallParticipant, Meeting
from ..services import calls
from ..services.call_signaling import send_signal
from ..services.meeting_hosts import host_ids, reachable_meeting
from ..services.participant_keys import (
    guest_key,
    guest_uuid_from_key,
    user_id_from_key,
    user_key,
)


@extend_schema(tags=["Chat - Meetings"])
class MeetingCallStateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Current call state for a meeting")
    def get(self, request, meeting_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        session = calls.get_active_call(calls.MeetingScope(meeting))
        if session is None:
            return Response({"active": False})
        return Response(calls.serialize_call_state(session))


@extend_schema(tags=["Chat - Meetings"])
class MeetingCallJoinView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Join or start the meeting call as a host", request=None)
    def post(self, request, meeting_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            session, _, _ = calls.start_or_join_call(
                request.user, calls.MeetingScope(meeting)
            )
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


@extend_schema(tags=["Chat - Meetings"])
class MeetingCallLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Leave the meeting call", request=None)
    def post(self, request, meeting_uuid):
        # No host gate: someone uninvited mid-call must still hang up cleanly.
        meeting = (
            Meeting.objects.select_related("event").filter(uuid=meeting_uuid).first()
        )
        if meeting is not None:
            calls.leave_call(request.user, calls.MeetingScope(meeting))
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Meetings"])
class MeetingCallHeartbeatView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Refresh call presence and media state")
    def post(self, request, meeting_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        scope = calls.MeetingScope(meeting)
        session = calls.get_active_call(scope)
        if session is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        media_state = request.data.get("media_state")
        if not isinstance(media_state, dict):
            media_state = dict(calls.DEFAULT_MEDIA_STATE)
        key = user_key(request.user.id)
        if calls.touch_presence(session.uuid, key, media_state):
            calls._broadcast(
                scope,
                "call_participant_updated",
                {
                    "session_id": str(session.uuid),
                    "participant_key": key,
                    "media_state": media_state,
                },
                exclude_key=key,
            )
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Meetings"])
class MeetingCallSignalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Relay a WebRTC signal to a peer of the meeting call")
    def post(self, request, meeting_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        to_participant = request.data.get("to_participant")
        signal = request.data.get("signal")
        if not isinstance(to_participant, str) or not isinstance(signal, dict):
            return Response(
                {"detail": "to_participant (string) and signal (object) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session = calls.get_active_call(calls.MeetingScope(meeting))
        if session is None:
            return Response(
                {"detail": "No active call."}, status=status.HTTP_400_BAD_REQUEST
            )
        target_user_id = user_id_from_key(to_participant)
        target_guest_uuid = guest_uuid_from_key(to_participant)
        if target_user_id is not None:
            target_key = user_key(target_user_id)
            target_ok = target_user_id in host_ids(meeting)
        elif target_guest_uuid is not None:
            target_key = guest_key(target_guest_uuid)
            target_ok = CallParticipant.objects.filter(
                session=session, guest_id=target_guest_uuid, left_at__isnull=True
            ).exists()
        else:
            target_key, target_ok = None, False
        if not target_ok:
            return Response(
                {"detail": "Target is not in this meeting."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        send_signal(session.uuid, target_key, user_key(request.user.id), signal)
        return Response({"status": "ok"})

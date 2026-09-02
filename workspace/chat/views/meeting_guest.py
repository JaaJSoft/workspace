"""Join, leave, heartbeat and state for an admitted meeting guest.

A guest reaches every view in this file from a bare /meet/<slug> link plus a
knock-issued token - no account, no session. ``permission_classes =
[AllowAny]`` is not enough on its own: DRF still runs SessionAuthentication by
default, which enforces CSRF for a signed-in visitor and populates
request.user, so a logged-in host previewing their own link would be treated
differently than an anonymous guest hitting the same URL. Every class below
also empties ``authentication_classes`` so the token is the only thing that
grants anything, for every caller alike.
"""

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..services import calls
from ..services.meeting_guests import guest_for_token, resolve_guest
from ..services.participant_keys import guest_key


def _guest_for_request(request, slug):
    """The admitted guest this request authorizes for this meeting, or None.

    The slug check is not redundant: without it, a token issued for meeting A
    would authorize a request against meeting B's slug, and every scoping
    decision downstream would silently use the wrong meeting.
    """
    token = request.headers.get("X-Meeting-Token", "")
    guest = resolve_guest(token)
    if guest is None or guest.meeting.slug != slug:
        return None
    return guest


def _guest_call_state(session):
    """serialize_call_state, minus what a guest must never see: the
    conversation id, which would let a guest address the host-side
    conversation endpoints directly."""
    data = calls.serialize_call_state(session)
    data.pop("conversation_id", None)
    return data


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestJoinView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Join the meeting's active call as an admitted guest")
    def post(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if calls.is_call_locked(guest.meeting.conversation_id):
            return Response(status=status.HTTP_423_LOCKED)

        try:
            session = calls.join_call_as_guest(guest)
        except calls.CallFull:
            return Response(
                {"detail": "Call is full."}, status=status.HTTP_409_CONFLICT
            )
        if session is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                "state": _guest_call_state(session),
                "ice_servers": getattr(settings, "CHAT_CALL_ICE_SERVERS", []),
            }
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestLeaveView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Leave the meeting's call", request=None)
    def post(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # No further gate beyond the token: leave_call_as_guest is a no-op
        # when there is no active call, same as the member CallLeaveView.
        calls.leave_call_as_guest(guest)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestHeartbeatView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Refresh a guest's call presence and media state")
    def post(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        session = calls.active_call_session_for_guest(guest)
        if session is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        media_state = request.data.get("media_state")
        if not isinstance(media_state, dict):
            media_state = dict(calls.DEFAULT_MEDIA_STATE)

        key = guest_key(guest.uuid)
        changed = calls.touch_presence(session.uuid, key, media_state)
        if changed:
            calls._broadcast(
                guest.meeting.conversation_id,
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
class MeetingGuestStateView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="The guest's own call state, or their lobby status")
    def get(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is not None:
            session = calls.active_call_session_for_guest(guest)
            if session is None:
                return Response({"active": False})
            return Response(
                {
                    **_guest_call_state(session),
                    "ice_servers": getattr(settings, "CHAT_CALL_ICE_SERVERS", []),
                }
            )

        # Not (or no longer) admitted: guest_for_token deliberately skips the
        # state/occurrence gate resolve_guest enforces, so a WAITING guest can
        # still be told their own lobby status. Still slug-scoped, same as
        # _guest_for_request, so a token for another meeting learns nothing.
        token = request.headers.get("X-Meeting-Token", "")
        lobby_guest = guest_for_token(token)
        if lobby_guest is None or lobby_guest.meeting.slug != slug:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(
            {"state": lobby_guest.state, "display_name": lobby_guest.display_name}
        )

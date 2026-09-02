"""Join, leave, heartbeat and state for an admitted meeting guest.

A guest reaches every view in this file from a bare /meet/<slug> link plus a
knock-issued token - no account, no session. ``permission_classes =
[AllowAny]`` is not enough on its own: DRF still runs SessionAuthentication by
default, which enforces CSRF for a signed-in visitor and populates
request.user, so a logged-in host previewing their own link would be treated
differently than an anonymous guest hitting the same URL. Every class below
also empties ``authentication_classes`` so the token is the only thing that
grants anything, for every caller alike.

Across this file, a 404 means exactly one thing: the token is missing,
unknown, revoked, or names a guest of a different meeting than *slug*. Every
other failure (no call to join yet, the call is full, the call is locked) has
its own status code, so a client can tell "you are not this meeting's guest"
apart from "you are, but something else is stopping you".
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
from ..throttling import MeetingGuestHeartbeatThrottle, MeetingPublicIpThrottle

# The only media_state keys chatCallMediaState() (call.js) ever produces.
# request.data is anonymous input reaching one shared cache value per session
# (touch_presence) that gets rebroadcast verbatim to every other participant,
# so unlike the member heartbeat this cannot trust an arbitrary dict through -
# unknown keys are dropped rather than the request rejected, since a stray key
# is just noise for a peer that does not recognize that media flag.
_KNOWN_MEDIA_STATE_KEYS = ("audio", "video", "screen")


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


def _sanitize_media_state(raw):
    """Coerce arbitrary guest input down to the known boolean media flags,
    falling back to the default when nothing usable survives."""
    if isinstance(raw, dict):
        cleaned = {key: bool(raw[key]) for key in _KNOWN_MEDIA_STATE_KEYS if key in raw}
        if cleaned:
            return cleaned
    return dict(calls.DEFAULT_MEDIA_STATE)


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestJoinView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MeetingPublicIpThrottle]

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
            # Not 404: the token is valid and the guest is admitted, there is
            # simply no call to join yet - a guest can never start one. Doing
            # so would mean an anonymous caller creating a CallSession, a
            # system Message in the host's conversation, and a call_started
            # broadcast: exactly the "anonymous caller drives a DB write and
            # a fan-out" property the never-call-get_active_call rule (see
            # is_call_locked, active_call_session_for_guest) exists to keep
            # out of this file.
            return Response(
                {"detail": "No call has been started in this meeting yet."},
                status=status.HTTP_409_CONFLICT,
            )

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
    throttle_classes = [MeetingPublicIpThrottle]

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
    # Its own scope, not MeetingPublicIpThrottle's: see
    # MeetingGuestHeartbeatThrottle's docstring for why the shared 30/min
    # budget does not fit a heartbeat firing every 5s.
    throttle_classes = [MeetingGuestHeartbeatThrottle]

    @extend_schema(summary="Refresh a guest's call presence and media state")
    def post(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        session = calls.active_call_session_for_guest(guest)
        if session is None:
            return Response(
                {"detail": "No call has been started in this meeting yet."},
                status=status.HTTP_409_CONFLICT,
            )

        media_state = _sanitize_media_state(request.data.get("media_state"))

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
    throttle_classes = [MeetingPublicIpThrottle]

    @extend_schema(summary="The guest's own call state, or their lobby status")
    def get(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is not None:
            session = calls.active_call_session_for_guest(guest)
            if session is None:
                return Response({"admitted": True, "active": False})
            return Response(
                {
                    "admitted": True,
                    **_guest_call_state(session),
                    "ice_servers": getattr(settings, "CHAT_CALL_ICE_SERVERS", []),
                }
            )

        # Not (or no longer) admitted through the real gate above:
        # guest_for_token deliberately skips the state/occurrence check
        # resolve_guest enforces, so a guest who is WAITING, REFUSED or
        # REMOVED - or whose ADMITTED row belongs to an occurrence the host
        # has since ended - can still be told their own status. Still
        # slug-scoped, same as _guest_for_request, so a token for another
        # meeting learns nothing either way.
        from ..models import MeetingGuest

        token = request.headers.get("X-Meeting-Token", "")
        lobby_guest = guest_for_token(token)
        if lobby_guest is None or lobby_guest.meeting.slug != slug:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if lobby_guest.state == MeetingGuest.State.ADMITTED:
            # end_meeting only sweeps WAITING rows to REFUSED (see its
            # docstring) - an ADMITTED row survives its own occurrence
            # closing verbatim, and resolve_guest above already rejected it
            # on the occurrence check. Report the meeting as ended rather
            # than parroting a DB status that stopped being true.
            reported_state = "ended"
        else:
            reported_state = lobby_guest.state
        return Response({"admitted": False, "state": reported_state})

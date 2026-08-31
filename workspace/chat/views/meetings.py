import logging

from django.conf import settings
from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none

from ..services import meetings as meeting_service
from ..services.calls import get_active_call
from ..services.conversations import get_active_membership
from ..services.meeting_guests import issue_token
from ..services.meeting_occurrences import current_occurrence

logger = logging.getLogger(__name__)


# ===========================================================================
# Public surface - no authentication of any kind. A guest reaches these from
# a bare /meet/<slug> link with no account, so the slug (and, on knock, the
# issued token) has to be the only thing that grants anything.
#
# AllowAny alone is not enough: DRF still runs SessionAuthentication by
# default, which enforces CSRF for a signed-in visitor and populates
# request.user - so a logged-in host previewing their own link would be
# treated differently than an anonymous guest hitting the same URL. Emptying
# the authentication list below removes that entirely, so every caller,
# signed in or not, is handled identically.
# ===========================================================================


class DisplayNameSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=80, allow_blank=False)


@extend_schema(tags=["Chat - Meetings"])
class MeetingSummaryView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Public summary of a meeting, by slug")
    def get(self, request, slug):
        from ..models import Meeting

        meeting = (
            Meeting.objects.select_related("event", "conversation")
            .filter(slug=slug)
            .first()
        )
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        session = get_active_call(meeting.conversation_id)
        locked = session.locked if session is not None else False
        return Response(
            {
                "title": meeting.event.title,
                "start": meeting.event.start,
                "locked": locked,
            }
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingKnockView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def _get_client_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    @extend_schema(
        summary="Knock to join a meeting's lobby", request=DisplayNameSerializer
    )
    def post(self, request, slug):
        from ..models import Meeting, MeetingGuest

        meeting = (
            Meeting.objects.select_related("event", "conversation")
            .filter(slug=slug)
            .first()
        )
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # A link outside its window looks like nothing, rather than leaking
        # that a meeting exists at all.
        occurrence = current_occurrence(meeting)
        if occurrence is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        occurrence_start, _occurrence_end = occurrence

        session = get_active_call(meeting.conversation_id)
        if session is not None and session.locked:
            return Response(status=status.HTTP_423_LOCKED)

        # Rate limit: max 10 knocks per IP per hour, mirroring
        # SharedPollVoteView's counter shape.
        ip = self._get_client_ip(request)
        rate_key = f"meeting_knock_rate:{meeting.uuid}:{ip}"
        attempts = cache.get(rate_key, 0)
        if attempts >= 10:
            return Response(
                {"detail": "Too many attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(rate_key, attempts + 1, 3600)

        waiting_count = MeetingGuest.objects.filter(
            meeting=meeting, state=MeetingGuest.State.WAITING
        ).count()
        if waiting_count >= settings.MEETING_MAX_WAITING_GUESTS:
            return Response(
                {"detail": "This meeting's lobby is full."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        ser = DisplayNameSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        display_name = ser.validated_data["display_name"]

        # occurrence_start must come from current_occurrence()'s own output,
        # never event.start verbatim - see meeting_occurrences.py's docstring.
        token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name=display_name,
            occurrence_start=occurrence_start,
            token_hash=token_hash,
        )
        logger.info(
            "Guest knocked on meeting %s from %s", scrub(str(meeting.uuid)), scrub(ip)
        )
        return Response(
            {
                "token": token,
                "state": guest.state,
                "display_name": guest.display_name,
            },
            status=status.HTTP_201_CREATED,
        )


# ===========================================================================
# Host endpoints - authenticated, membership-gated on the meeting's dedicated
# conversation. "Host" is not a role: it is any active member of that
# conversation, the same gate every other chat endpoint uses.
# ===========================================================================


def _meeting_for_host(request, meeting_uuid):
    """The meeting this user may act on, or None.

    A host is any active member of the meeting's conversation. The conversation
    is dedicated to the meeting and seeded from the event's members, so "may act
    on this meeting" and "is an internal participant of it" are the same
    question, answered by the gate every other chat endpoint already uses.
    """
    from ..models import Meeting

    meeting = (
        Meeting.objects.select_related("event", "conversation")
        .filter(uuid=meeting_uuid)
        .first()
    )
    if meeting is None:
        return None
    if not get_active_membership(request.user, meeting.conversation_id):
        return None
    return meeting


def _guest_for_host(request, meeting_uuid, guest_uuid):
    """The (meeting, guest) pair this host may act on. Either may be None."""
    from ..models import MeetingGuest

    meeting = _meeting_for_host(request, meeting_uuid)
    if meeting is None:
        return None, None
    guest = MeetingGuest.objects.filter(uuid=guest_uuid, meeting=meeting).first()
    return meeting, guest


@extend_schema(tags=["Chat - Meetings"])
class MeetingCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Create (or return) the meeting for an event")
    def post(self, request):
        from workspace.calendar.models import Event

        event_uuid = parse_uuid_or_none(request.data.get("event_id"))
        if event_uuid is None:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event = Event.objects.filter(uuid=event_uuid).first()
        if event is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if event.owner_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)

        meeting = meeting_service.create_meeting(event, request.user)
        return Response(
            {
                "uuid": str(meeting.uuid),
                "slug": meeting.slug,
                "join_url": request.build_absolute_uri(meeting.join_path),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingLobbyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List guests waiting in the meeting's lobby")
    def get(self, request, meeting_uuid):
        from ..models import MeetingGuest

        meeting = _meeting_for_host(request, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        guests = MeetingGuest.objects.filter(
            meeting=meeting, state=MeetingGuest.State.WAITING
        ).order_by("created_at")
        return Response(
            [
                {
                    "uuid": str(g.uuid),
                    "display_name": g.display_name,
                    "created_at": g.created_at,
                }
                for g in guests
            ]
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestAdmitView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Admit a waiting guest", request=None)
    def post(self, request, meeting_uuid, guest_uuid):
        meeting, guest = _guest_for_host(request, meeting_uuid, guest_uuid)
        if meeting is None or guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        meeting_service.admit_guest(guest, request.user)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestRefuseView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Refuse a waiting guest", request=None)
    def post(self, request, meeting_uuid, guest_uuid):
        meeting, guest = _guest_for_host(request, meeting_uuid, guest_uuid)
        if meeting is None or guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        meeting_service.refuse_guest(guest)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestRemoveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove an admitted guest", request=None)
    def post(self, request, meeting_uuid, guest_uuid):
        meeting, guest = _guest_for_host(request, meeting_uuid, guest_uuid)
        if meeting is None or guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        meeting_service.remove_guest(guest)
        return Response({"status": "ok"})


@extend_schema(tags=["Chat - Meetings"])
class MeetingLockView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Lock or unlock the meeting's active call")
    def post(self, request, meeting_uuid):
        meeting = _meeting_for_host(request, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        locked = is_truthy(request.data.get("locked"))
        if not meeting_service.set_locked(meeting, locked):
            return Response(status=status.HTTP_409_CONFLICT)
        return Response({"locked": locked})


@extend_schema(tags=["Chat - Meetings"])
class MeetingEndView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="End the meeting's current occurrence", request=None)
    def post(self, request, meeting_uuid):
        meeting = _meeting_for_host(request, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not meeting_service.end_meeting(meeting):
            return Response(status=status.HTTP_409_CONFLICT)
        return Response({"status": "ok"})

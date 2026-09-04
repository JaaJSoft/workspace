import logging
import unicodedata

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub
from workspace.common.rate_limit import increment_counter
from workspace.common.request_ip import client_ip
from workspace.common.uuids import parse_uuid_or_none

from ..services import meetings as meeting_service
from ..services.calls import is_call_locked
from ..services.conversations import get_active_membership
from ..services.meeting_guests import issue_token
from ..services.meeting_occurrences import current_occurrence
from ..throttling import MeetingPublicIpThrottle

logger = logging.getLogger(__name__)

# Bidi formatting characters that can visually rewrite a display name (e.g.
# a right-to-left override making "ecilA" render as "Alice"). Stripped in
# DisplayNameSerializer below alongside plain control characters, since the
# name is shown verbatim to a host in the admit prompt. Listed as code points
# (not string literals) so no editor or diff tool can silently normalize or
# hide the very characters this exists to catch.
_BIDI_CONTROL_CODEPOINTS = frozenset(
    {
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
        0x061C,
    }
)


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

    def validate_display_name(self, value):
        cleaned = "".join(
            ch
            for ch in value
            if ord(ch) not in _BIDI_CONTROL_CODEPOINTS
            and unicodedata.category(ch) != "Cc"
        ).strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


@extend_schema(tags=["Chat - Meetings"])
class MeetingSummaryView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MeetingPublicIpThrottle]

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

        # The series master start is only correct for a non-recurring event;
        # a recurring meeting's stable link must report the occurrence that
        # is actually reachable right now, the same resolution the knock
        # endpoint below already relies on. No occurrence being reachable
        # (nothing upcoming, or the host has ended the series) falls back to
        # the master start rather than hiding the meeting: this endpoint
        # discloses a meeting's existence and timing unconditionally, so
        # there is nothing left to hide by omitting the start too.
        occurrence = current_occurrence(meeting)
        start = occurrence[0] if occurrence is not None else meeting.event.start

        # Only ever the resolved occurrence, never the event.start fallback:
        # a durable lock names an occurrence, and there is none to name when
        # nothing is reachable.
        locked = is_call_locked(
            meeting.conversation_id,
            occurrence[0] if occurrence is not None else None,
        )
        return Response(
            {
                "title": meeting.event.title,
                "start": start,
                "locked": locked,
            }
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingKnockView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MeetingPublicIpThrottle]

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

        # This 404 is a UX signal ("nothing to knock into right now"), not a
        # security control: MeetingSummaryView above discloses this same
        # meeting's existence and timing regardless of the window, so hiding
        # it here would buy nothing. A client just cannot tell "too early"
        # from "bad link" apart, and does not need to.
        occurrence = current_occurrence(meeting)
        if occurrence is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        occurrence_start, _occurrence_end = occurrence

        # The host ending this occurrence must close the door on new knocks
        # too, not just on tokens already issued (that's resolve_guest's
        # job) - current_occurrence deliberately does not consult this, so
        # it is checked here explicitly.
        if meeting.closed_occurrence_start == occurrence_start:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if is_call_locked(meeting.conversation_id, occurrence_start):
            return Response(status=status.HTTP_423_LOCKED)

        # Rate limit: max 10 knocks per IP per hour, mirroring
        # SharedPollVoteView's counter shape.
        ip = client_ip(request)
        rate_key = f"meeting_knock_rate:{meeting.uuid}:{ip}"
        attempts = increment_counter(rate_key, 3600)
        if attempts > 10:
            return Response(
                {"detail": "Too many attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Scoped to this occurrence: the slug is stable for the whole
        # series, so an unscoped count would let WAITING rows from past
        # occurrences - which nothing ever purges - permanently eat the cap
        # for every occurrence after them.
        waiting_count = MeetingGuest.objects.filter(
            meeting=meeting,
            state=MeetingGuest.State.WAITING,
            occurrence_start=occurrence_start,
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
            "Guest knocked on meeting %s from %s",
            scrub(str(meeting.uuid)),
            scrub(ip)[:64],
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

        # 404 either way (unknown event, or one that exists but belongs to
        # someone else): every other route in this file refuses to
        # distinguish "not yours" from "does not exist", and a lone
        # exception here would be a trap for whoever edits this file next.
        event = Event.objects.filter(uuid=event_uuid, owner=request.user).first()
        if event is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

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

        # Scoped to the current occurrence, same reasoning as the knock
        # cap above: an unscoped list would keep surfacing WAITING rows from
        # past occurrences that nothing ever purges, and admitting one
        # produces a guest resolve_guest will always reject.
        occurrence = current_occurrence(meeting)
        if occurrence is None:
            return Response([])
        occurrence_start, _occurrence_end = occurrence

        guests = MeetingGuest.objects.filter(
            meeting=meeting,
            state=MeetingGuest.State.WAITING,
            occurrence_start=occurrence_start,
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
        meeting_service.set_locked(meeting, locked)
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

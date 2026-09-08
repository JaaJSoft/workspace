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

from ..services import calls
from ..services import meetings as meeting_service
from ..services.calls import is_call_locked
from ..services.identities import display_name_for_identity
from ..services.meeting_guests import issue_token
from ..services.meeting_hosts import is_host, public_meeting, reachable_meeting
from ..services.meeting_occurrences import current_occurrence
from ..services.participant_keys import guest_key
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
# Public surface - reached from a bare /meet/<slug> link with no account, so
# the slug (and, on knock, the issued token) has to be the only thing that
# grants anything.
#
# MeetingSummaryView is anonymous outright: AllowAny alone is not enough,
# because DRF still runs SessionAuthentication by default, which enforces
# CSRF for a signed-in visitor and populates request.user - so a logged-in
# host previewing their own link would be treated differently than an
# anonymous guest hitting the same URL. Emptying its authentication list
# removes that entirely, so every caller, signed in or not, is handled
# identically. MeetingKnockView keeps the default authentication instead -
# see its own docstring for why.
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
        meeting = public_meeting(slug)
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
        start = (
            occurrence[0]
            if occurrence is not None
            else (meeting.event.start if meeting.event_id else None)
        )

        # Only ever the resolved occurrence, never the event.start fallback:
        # a durable lock names an occurrence, and there is none to name when
        # nothing is reachable.
        scope = calls.MeetingScope(meeting)
        locked = is_call_locked(
            scope,
            occurrence[0] if occurrence is not None else None,
        )

        # Plain read only: this view is AllowAny, so calls.get_active_call's
        # self-heal (select_for_update, can end a stale session and
        # broadcast) must never run off an anonymous GET.
        session = calls.active_call_session(scope)
        participant_count = (
            calls.active_participant_count(session) if session is not None else 0
        )
        return Response(
            {
                "title": meeting.title,
                "start": start,
                "locked": locked,
                "max_participants": calls.max_participants(),
                "participant_count": participant_count,
            }
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingKnockView(APIView):
    """Knock to join a meeting's lobby.

    Anonymous for a stranger; a signed-in visitor is bound to the row, so
    the session and its CSRF check apply to them.
    """

    permission_classes = [AllowAny]
    throttle_classes = [MeetingPublicIpThrottle]

    @extend_schema(
        summary="Knock to join a meeting's lobby", request=DisplayNameSerializer
    )
    def post(self, request, slug):
        from ..models import MeetingGuest

        # Only the door to newcomers: tokens already issued keep resolving
        # through guest_for_slug, so turning the link off never evicts a
        # guest who is already inside.
        meeting = public_meeting(slug)
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

        if is_call_locked(calls.MeetingScope(meeting), occurrence_start):
            return Response(status=status.HTTP_423_LOCKED)

        # A host already has the run endpoints for their own meeting; a
        # knock is how a stranger asks to be let in, so a host knocking on
        # their own room is a caller error, not a lobby entry.
        user = request.user if request.user.is_authenticated else None
        if user is not None and is_host(user, meeting):
            return Response(
                {"detail": "You host this meeting."}, status=status.HTTP_409_CONFLICT
            )

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

        # A signed-in stranger is bound to their own account, not whatever
        # name they typed - the posted display_name is ignored for them, the
        # same way a host's display name would be if they were allowed to
        # knock at all.
        if user is not None:
            display_name = display_name_for_identity(user, None)[:80]
        else:
            ser = DisplayNameSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            display_name = ser.validated_data["display_name"]

        # occurrence_start must come from current_occurrence()'s own output,
        # never event.start verbatim - see meeting_occurrences.py's docstring.
        token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=meeting,
            user=user,
            display_name=display_name,
            occurrence_start=occurrence_start,
            token_hash=token_hash,
        )
        logger.info(
            "Guest knocked on meeting %s from %s",
            scrub(str(meeting.uuid)),
            scrub(ip)[:64],
        )
        meeting_service.notify_hosts(
            meeting,
            "meeting_guest_waiting",
            {
                "meeting_id": str(meeting.uuid),
                "guest_uuid": str(guest.uuid),
                "display_name": guest.display_name,
                "user_id": guest.user_id,
            },
        )
        return Response(
            {
                "token": token,
                "state": guest.state,
                "display_name": guest.display_name,
                "participant_key": guest_key(guest.uuid),
                "guest_uuid": str(guest.uuid),
                "user_id": guest.user_id,
            },
            status=status.HTTP_201_CREATED,
        )


# ===========================================================================
# Host endpoints - authenticated, gated on meeting_hosts.is_host.
# ===========================================================================


def _meeting_for_host(request, meeting_uuid):
    """The meeting this user may act on, or None (404 either way)."""
    return reachable_meeting(request.user, meeting_uuid)


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

    @extend_schema(summary="Create (or return) a meeting, from an event or ad hoc")
    def post(self, request):
        from workspace.calendar.models import Event

        raw_event_id = request.data.get("event_id")
        if raw_event_id is None:
            title = str(request.data.get("title", "")).strip()
            if not title:
                return Response(
                    {"detail": "title is required for a meeting without an event."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            meeting = meeting_service.create_ad_hoc_meeting(request.user, title)
        else:
            event_uuid = parse_uuid_or_none(raw_event_id)
            if event_uuid is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)
            # 404 either way (unknown event, or one that exists but belongs
            # to someone else): every other route in this file refuses to
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

        guests = (
            MeetingGuest.objects.select_related("user")
            .filter(
                meeting=meeting,
                state=MeetingGuest.State.WAITING,
                occurrence_start=occurrence_start,
            )
            .order_by("created_at")
        )
        return Response(
            [
                {
                    "uuid": str(g.uuid),
                    "display_name": g.display_name,
                    "created_at": g.created_at,
                    "user_id": g.user_id,
                    "username": g.user.username if g.user_id else None,
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

        requested = is_truthy(request.data.get("locked"))
        # The committed state, not the request: locking a meeting with no
        # reachable occurrence and no active call stores nothing, and saying
        # otherwise would contradict the summary endpoint immediately.
        locked = meeting_service.set_locked(meeting, requested)
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

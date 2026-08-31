from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.uuids import parse_uuid_or_none

from ..services import meetings as meeting_service
from ..services.conversations import get_active_membership

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

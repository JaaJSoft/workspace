"""The hosts' side of the meeting chat."""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from ..models import MeetingMessage
from ..services import meeting_messages as chat_service
from ..services.meeting_hosts import reachable_meeting


def clean_body(data):
    """(body, error) for a posted message, shared with the guest view."""
    body = str(data.get("body", "")).strip()
    if not body:
        return None, "Message must have text."
    if len(body) > chat_service.MEETING_MESSAGE_MAX_LENGTH:
        return None, "Message is too long."
    return body, None


def page_args(query_params):
    before = parse_uuid_or_none(query_params.get("before"))
    try:
        limit = min(max(int(query_params.get("limit", 50)), 1), 100)
    except TypeError, ValueError:
        limit = 50
    return before, limit


@extend_schema(tags=["Chat - Meetings"])
class MeetingMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="The meeting's chat, newest page first")
    def get(self, request, meeting_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        before, limit = page_args(request.query_params)
        rows, has_more = chat_service.messages_for_host(
            meeting, before=before, limit=limit
        )
        return Response(
            {
                "messages": [chat_service.serialize_message(m) for m in rows],
                "has_more": has_more,
            }
        )

    @extend_schema(summary="Post a line to the meeting chat as a host")
    def post(self, request, meeting_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body, error = clean_body(request.data)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        message = chat_service.post_message(meeting, body, author=request.user)
        return Response(
            chat_service.serialize_message(message), status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Chat - Meetings"])
class MeetingMessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Delete a line of the meeting chat (hosts only)")
    def delete(self, request, meeting_uuid, message_uuid):
        meeting = reachable_meeting(request.user, meeting_uuid)
        if meeting is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        message = MeetingMessage.objects.filter(
            uuid=message_uuid, meeting=meeting
        ).first()
        if message is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        chat_service.delete_message(message)
        return Response(status=status.HTTP_204_NO_CONTENT)

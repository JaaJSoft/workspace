"""Join, leave, heartbeat and state for an admitted meeting guest.

A guest reaches every view in this file from a bare /meet/<slug> link plus a
knock-issued token - no account, no session. ``permission_classes =
[AllowAny]`` is not enough on its own: DRF still runs SessionAuthentication by
default, which enforces CSRF for a signed-in visitor and populates
request.user, so a logged-in host previewing their own link would be treated
differently than an anonymous guest hitting the same URL. Every class below
also empties ``authentication_classes`` so the token is the only thing that
grants anything, for every caller alike.

On the three content endpoints (join, leave, heartbeat), a 404 means
exactly one thing: the token is missing, unknown, revoked, or names a guest
of a different meeting than *slug*. Every other failure on those three (no
call to join yet, the call is full, the call is locked) has its own status
code, so a client can tell "you are not this meeting's guest" apart from
"you are, but something else is stopping you".

state is the deliberate exception, and only a partial one: an unknown token
or one naming a guest of a different meeting still 404s there too, same as
the other three. What state does differently is a token that fails the
admitted-and-current check yet still names a real guest of *this* meeting
(waiting, refused, removed, or admitted to an occurrence the host has since
ended) - that gets 200 with a body describing the guest's own status,
never 404, because a guest has to be able to learn their own status,
including a revoked one, without the response itself looking like a dead
end.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.http import StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none

from ..models import CallParticipant, Conversation, Message
from ..serializers import GuestMessageSerializer, MessageCreateSerializer
from ..services import calls
from ..services.call_signaling import send_signal
from ..services.guest_messages import message_queryset
from ..services.guest_stream import stream_guest_events
from ..services.meeting_guests import guest_for_token, resolve_guest
from ..services.mentions import build_mention_map
from ..services.participant_keys import (
    guest_key,
    guest_uuid_from_key,
    user_id_from_key,
    user_key,
)
from ..services.posting import deliver_message
from ..services.rendering import render_message_body
from ..services.threads import resolve_thread_root
from ..throttling import (
    MeetingGuestHeartbeatThrottle,
    MeetingGuestSignalThrottle,
    MeetingPublicIpThrottle,
)

logger = logging.getLogger(__name__)

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
class MeetingGuestSignalView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    # Its own scope, not MeetingPublicIpThrottle's: see
    # MeetingGuestSignalThrottle's docstring for why the shared 30/min budget
    # does not fit the offer/answer + ICE trickle burst a call join produces.
    throttle_classes = [MeetingGuestSignalThrottle]

    @extend_schema(summary="Relay a WebRTC signal to a peer in the guest's call")
    def post(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        session = calls.active_call_session_for_guest(guest)
        if session is None:
            return Response(
                {"detail": "No active call."}, status=status.HTTP_400_BAD_REQUEST
            )

        own_key = guest_key(guest.uuid)
        is_own_active_participant = CallParticipant.objects.filter(
            session=session, guest=guest, left_at__isnull=True
        ).exists()
        if not is_own_active_participant:
            return Response(
                {"detail": "Join the call before signalling."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_participant = request.data.get("to_participant")
        signal = request.data.get("signal")
        if not isinstance(to_participant, str) or not isinstance(signal, dict):
            return Response(
                {"detail": "to_participant (string) and signal (object) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A guest may reach a member or another guest, but only one who is
        # themselves an active participant of this SAME session - which is
        # already scoped to this guest's own meeting, so this single check
        # also rejects any target from a different meeting's call.
        target_user_id = user_id_from_key(to_participant)
        target_guest_uuid = guest_uuid_from_key(to_participant)
        if target_user_id is not None:
            target_key = user_key(target_user_id)
            target_is_active = CallParticipant.objects.filter(
                session=session, user_id=target_user_id, left_at__isnull=True
            ).exists()
        elif target_guest_uuid is not None:
            target_key = guest_key(target_guest_uuid)
            target_is_active = CallParticipant.objects.filter(
                session=session, guest_id=target_guest_uuid, left_at__isnull=True
            ).exists()
        else:
            target_key = None
            target_is_active = False

        if not target_is_active:
            return Response(
                {"detail": "Target is not a participant of this call."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        send_signal(session.uuid, target_key, own_key, signal)
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


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestStreamView(APIView):
    """Server-sent events for a meeting guest: lobby status, call signalling
    and messages.

    Gated on ``guest_for_token`` (the WAITING-tolerant lookup), not
    ``resolve_guest`` like every other view in this file - deliberately
    wider. A WAITING guest has to be able to hold a stream open to learn
    they were admitted; gating on ``resolve_guest`` here would 404 that
    guest before the generator ever runs, which is exactly what made
    ``meeting_refused`` undeliverable before this view existed (see
    ``guest_stream``'s module docstring). The generator itself still fences
    every piece of *content*: ``call_*`` events and messages are only ever
    forwarded once its own per-cycle ``resolve_guest`` call succeeds, so a
    WAITING/REFUSED/REMOVED guest's stream carries nothing but the four
    ``meeting_*`` lifecycle events.

    This does widen the unauthenticated, long-lived surface: any token that
    merely names a real guest of this meeting - including one never
    admitted - can now hold a connection open. That is bounded the same way
    the lobby itself is bounded: the knock endpoint caps new tokens at
    10/IP/hour, and ``MEETING_MAX_WAITING_GUESTS`` (20) caps how many WAITING
    rows - and so how many such streams - a single occurrence can have at
    once.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MeetingPublicIpThrottle]

    @extend_schema(summary="Server-sent event stream for a meeting guest")
    def get(self, request, slug):
        token = request.headers.get("X-Meeting-Token", "")
        lobby_guest = guest_for_token(token)
        if lobby_guest is None or lobby_guest.meeting.slug != slug:
            return Response(status=status.HTTP_404_NOT_FOUND)

        last_event_id = request.META.get("HTTP_LAST_EVENT_ID")
        response = StreamingHttpResponse(
            stream_guest_events(
                token, lobby_guest.meeting_id, last_event_id=last_event_id
            ),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        response.streaming = True
        response["Content-Encoding"] = "identity"
        return response


@extend_schema(tags=["Chat - Meetings"])
class MeetingGuestMessagesView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MeetingPublicIpThrottle]

    @extend_schema(
        summary="List messages in the guest's meeting, floored to their occurrence"
    )
    def get(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        conversation_id = guest.meeting.conversation_id
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 100)
        except TypeError, ValueError:
            limit = 50
        before = request.query_params.get("before")

        # Floored to guest.occurrence_start - the value resolve_guest already
        # validated for this token - never recomputed here. A guest must never
        # read the conversation's history from before their occurrence opened.
        messages = message_queryset().filter(
            conversation_id=conversation_id, created_at__gte=guest.occurrence_start
        )

        if before:
            before_uuid = parse_uuid_or_none(before)
            if before_uuid is None:
                logger.debug("Ignoring malformed ?before cursor: %s", scrub(before))
            else:
                cursor_msg = (
                    Message.objects.filter(
                        conversation_id=conversation_id,
                        uuid=before_uuid,
                        created_at__gte=guest.occurrence_start,
                    )
                    .only("created_at")
                    .first()
                )
                if cursor_msg is not None:
                    messages = messages.filter(created_at__lt=cursor_msg.created_at)
                else:
                    logger.debug("Ignoring unknown ?before cursor: %s", scrub(before))

        messages = list(messages.order_by("-created_at")[: limit + 1])
        has_more = len(messages) > limit
        if has_more:
            messages = messages[:limit]
        messages.reverse()

        serializer = GuestMessageSerializer(
            messages, many=True, context={"floor": guest.occurrence_start}
        )
        return Response({"messages": serializer.data, "has_more": has_more})

    @extend_schema(
        summary="Post a message as an admitted meeting guest",
        request=MessageCreateSerializer,
    )
    def post(self, request, slug):
        guest = _guest_for_request(request, slug)
        if guest is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # No attachment support for a guest (see the module docstring's
        # posting section for why): silently dropping file_uuids/duration
        # would hide that boundary behind a message with no attachment and
        # no explanation. Reject instead, so it is visible from the API.
        if serializer.validated_data.get("file_uuids") or (
            serializer.validated_data.get("duration") is not None
        ):
            return Response(
                {"detail": "Guest messages do not support file uploads."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body = serializer.validated_data.get("body", "").strip()
        if not body:
            return Response(
                {"detail": "Message must have text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # conversation_id always comes from the guest's own meeting - never
        # from the request body, which carries no such field to begin with
        # (MessageCreateSerializer has none). That is what keeps a guest from
        # ever naming a conversation to write into.
        conversation_id = guest.meeting.conversation_id

        mention_map, has_everyone = build_mention_map(body)
        mentioned_user_ids = {uid for uid in mention_map.values() if uid}
        body_html = render_message_body(body, mention_map=mention_map or None)

        reply_to = None
        reply_to_uuid = serializer.validated_data.get("reply_to_uuid")
        if reply_to_uuid:
            try:
                # Floored the same as the read side: a reply target below
                # guest.occurrence_start is refused exactly like one in
                # another conversation - otherwise a guest could read a
                # pre-window UUID off an in-window reply's (unfloored)
                # thread_root and use it here to pull that pre-window
                # message's excerpt back through the 201 response, and to
                # bump its reply_count/last_reply_at besides.
                reply_to = Message.objects.get(
                    uuid=reply_to_uuid,
                    conversation_id=conversation_id,
                    deleted_at__isnull=True,
                    created_at__gte=guest.occurrence_start,
                )
            except Message.DoesNotExist:
                return Response(
                    {"detail": "Reply target message not found in this conversation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        thread_root = resolve_thread_root(reply_to) if reply_to else None
        # The reply target itself can be in-window while the thread it
        # belongs to is not: resolve_thread_root hops straight to that root,
        # one step past the floor already checked above. A guest may see the
        # in-window reply but not the thread it is part of, so refuse rather
        # than store the reply unthreaded (which would break the "replies
        # flatten onto one root" invariant) - same failure as any other
        # below-floor target.
        if thread_root is not None and thread_root.created_at < guest.occurrence_start:
            return Response(
                {"detail": "Reply target message not found in this conversation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            message = Message.objects.create(
                conversation_id=conversation_id,
                guest=guest,
                body=body,
                body_html=body_html,
                reply_to=reply_to,
                thread_root=thread_root,
            )
            conversation = Conversation.objects.get(pk=conversation_id)
            deliver_message(
                conversation,
                message,
                mentioned_user_ids=mentioned_user_ids,
                mention_everyone=has_everyone,
            )

        # Deliberately no AI bot trigger here, unlike MessageListView.post:
        # an unauthenticated guest must never drive a billable LLM call or
        # write into the host's conversation under a bot identity. This is a
        # permanent omission, not a gap to fill in.

        if body:
            from ..services.link_preview import extract_urls

            urls = extract_urls(body)
            if urls:
                from ..tasks import fetch_link_previews

                fetch_link_previews.delay(str(message.pk), urls)

        msg = message_queryset().filter(pk=message.pk).first()
        response_serializer = GuestMessageSerializer(
            msg, context={"floor": guest.occurrence_start}
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

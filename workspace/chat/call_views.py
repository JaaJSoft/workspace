from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .call_models import Call, CallParticipant
from .call_serializers import CallMuteSerializer, CallSignalSerializer
from .call_services import (
    CallError,
    generate_ice_servers,
    join_call,
    leave_call,
    reject_call,
    relay_signal,
    start_call,
    update_mute,
)
from .models import Conversation, ConversationMember


def _is_member(user, conversation_id):
    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user=user,
        left_at__isnull=True,
    ).exists()


@extend_schema(tags=['Chat / Voice Calls'])
class CallStartView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Start a voice call")
    def post(self, request, conversation_id):
        if not _is_member(request.user, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            conv = Conversation.objects.get(uuid=conversation_id)
        except Conversation.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            call = start_call(conversation=conv, initiator=request.user)
        except IntegrityError:
            return Response({'error': 'A call is already active in this conversation'}, status=status.HTTP_409_CONFLICT)
        return Response({'call_id': str(call.uuid), 'ice_servers': generate_ice_servers()}, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Chat / Voice Calls'])
class CallJoinView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Join a voice call")
    def post(self, request, conversation_id):
        if not _is_member(request.user, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)
        call = Call.objects.filter(conversation_id=conversation_id, status__in=['ringing', 'active']).first()
        if not call:
            return Response({'error': 'No active call'}, status=status.HTTP_404_NOT_FOUND)
        try:
            join_call(call=call, user=request.user)
        except CallError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        participants = list(CallParticipant.objects.filter(call=call, left_at__isnull=True).values_list('user__id', 'user__username'))
        return Response({'call_id': str(call.uuid), 'participants': [{'id': uid, 'name': name} for uid, name in participants], 'ice_servers': generate_ice_servers()})


@extend_schema(tags=['Chat / Voice Calls'])
class CallLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Leave a voice call")
    def post(self, request, conversation_id):
        if not _is_member(request.user, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)
        call = Call.objects.filter(conversation_id=conversation_id, status__in=['ringing', 'active']).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)
        leave_call(call=call, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat / Voice Calls'])
class CallRejectView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Reject an incoming call")
    def post(self, request, conversation_id):
        if not _is_member(request.user, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)
        call = Call.objects.filter(conversation_id=conversation_id, status__in=['ringing', 'active']).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)
        reject_call(call=call, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat / Voice Calls'])
class CallSignalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Relay WebRTC signaling")
    def post(self, request, conversation_id):
        if not _is_member(request.user, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = CallSignalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call = Call.objects.filter(conversation_id=conversation_id, status__in=['ringing', 'active']).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            relay_signal(call=call, from_user=request.user, to_user_id=serializer.validated_data['to_user'], signal_type=serializer.validated_data['type'], payload=serializer.validated_data['payload'])
        except CallError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat / Voice Calls'])
class CallMuteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Update mute state")
    def post(self, request, conversation_id):
        if not _is_member(request.user, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = CallMuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call = Call.objects.filter(conversation_id=conversation_id, status='active').first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)
        update_mute(call=call, user=request.user, muted=serializer.validated_data['muted'])
        return Response(status=status.HTTP_204_NO_CONTENT)

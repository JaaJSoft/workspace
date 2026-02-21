from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Calendar, Event, EventMember, Poll, PollSlot, PollVote
from .serializers_polls import (
    GuestVoteSubmitSerializer,
    PollCreateSerializer,
    PollFinalizeSerializer,
    PollListSerializer,
    PollSerializer,
    PollUpdateSerializer,
    VoteSubmitSerializer,
)


@extend_schema(tags=['Calendar - Polls'])
class PollListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List polls",
        parameters=[
            {'name': 'filter', 'in': 'query', 'schema': {'type': 'string', 'enum': ['mine', 'shared']}},
            {'name': 'status', 'in': 'query', 'schema': {'type': 'string', 'enum': ['open', 'closed', 'all']}},
        ],
        responses=PollListSerializer(many=True),
    )
    def get(self, request):
        filter_by = request.query_params.get('filter', 'mine')
        status_by = request.query_params.get('status', 'open')

        if filter_by == 'shared':
            # Polls where user voted but didn't create
            voted_poll_ids = (
                PollVote.objects
                .filter(user=request.user)
                .values_list('slot__poll_id', flat=True)
                .distinct()
            )
            polls = Poll.objects.filter(uuid__in=voted_poll_ids).exclude(created_by=request.user)
        else:
            polls = Poll.objects.filter(created_by=request.user)

        if status_by != 'all':
            polls = polls.filter(status=status_by)

        return Response(PollListSerializer(polls, many=True).data)

    @extend_schema(summary="Create a poll", request=PollCreateSerializer, responses=PollSerializer)
    def post(self, request):
        ser = PollCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        poll = Poll.objects.create(
            title=d['title'],
            description=d['description'],
            created_by=request.user,
        )

        for i, slot_data in enumerate(d['slots']):
            PollSlot.objects.create(
                poll=poll,
                start=slot_data['start'],
                end=slot_data.get('end'),
                position=i,
            )

        return Response(
            PollSerializer(poll, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Calendar - Polls'])
class PollDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_poll(self, poll_id, user):
        return get_object_or_404(Poll, uuid=poll_id, created_by=user)

    @extend_schema(summary="Get poll detail", responses=PollSerializer)
    def get(self, request, poll_id):
        poll = get_object_or_404(Poll, uuid=poll_id)
        return Response(PollSerializer(poll, context={'request': request}).data)

    @extend_schema(summary="Update poll", request=PollUpdateSerializer, responses=PollSerializer)
    def patch(self, request, poll_id):
        poll = self._get_poll(poll_id, request.user)
        ser = PollUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        for field, value in ser.validated_data.items():
            setattr(poll, field, value)
        poll.save()
        return Response(PollSerializer(poll, context={'request': request}).data)

    @extend_schema(summary="Delete poll")
    def delete(self, request, poll_id):
        poll = self._get_poll(poll_id, request.user)
        poll.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Calendar - Polls'])
class PollVoteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Submit votes", request=VoteSubmitSerializer, responses=PollSerializer)
    def post(self, request, poll_id):
        poll = get_object_or_404(Poll, uuid=poll_id, status=Poll.Status.OPEN)
        ser = VoteSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        slot_ids = {v['slot_id'] for v in ser.validated_data['votes']}
        valid_slots = set(
            poll.slots.filter(uuid__in=slot_ids).values_list('uuid', flat=True)
        )

        for vote_data in ser.validated_data['votes']:
            if vote_data['slot_id'] not in valid_slots:
                continue
            PollVote.objects.update_or_create(
                slot_id=vote_data['slot_id'],
                user=request.user,
                defaults={'choice': vote_data['choice']},
            )

        return Response(PollSerializer(poll, context={'request': request}).data)


@extend_schema(tags=['Calendar - Polls'])
class PollFinalizeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Finalize poll", request=PollFinalizeSerializer, responses=PollSerializer)
    def post(self, request, poll_id):
        poll = get_object_or_404(
            Poll, uuid=poll_id, created_by=request.user, status=Poll.Status.OPEN,
        )
        ser = PollFinalizeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        slot = get_object_or_404(PollSlot, uuid=ser.validated_data['slot_id'], poll=poll)

        # Find or create default calendar
        calendar = Calendar.objects.filter(owner=request.user).order_by('name').first()
        if not calendar:
            calendar = Calendar.objects.create(
                owner=request.user, name='Personal', color='primary',
            )

        # Create event
        event = Event.objects.create(
            calendar=calendar,
            title=poll.title,
            description=poll.description,
            start=slot.start,
            end=slot.end,
            owner=request.user,
        )

        # Add members from yes/maybe votes (authenticated users only)
        voter_ids = (
            PollVote.objects
            .filter(slot=slot, choice__in=['yes', 'maybe'], user__isnull=False)
            .exclude(user=request.user)
            .values_list('user_id', flat=True)
            .distinct()
        )
        EventMember.objects.bulk_create([
            EventMember(event=event, user_id=uid, status=EventMember.Status.ACCEPTED)
            for uid in voter_ids
        ])

        # Close poll
        poll.status = Poll.Status.CLOSED
        poll.chosen_slot = slot
        poll.event = event
        poll.save()

        return Response(PollSerializer(poll, context={'request': request}).data)


# -- Public (shared link) views --

@extend_schema(tags=['Calendar - Polls (Public)'])
class SharedPollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Get poll via share link", responses=PollSerializer)
    def get(self, request, token):
        poll = get_object_or_404(Poll, share_token=token)
        return Response(PollSerializer(poll, context={'request': request}).data)


@extend_schema(tags=['Calendar - Polls (Public)'])
class SharedPollVoteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Submit guest votes", request=GuestVoteSubmitSerializer, responses=PollSerializer)
    def post(self, request, token):
        poll = get_object_or_404(Poll, share_token=token, status=Poll.Status.OPEN)
        ser = GuestVoteSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        slot_ids = {v['slot_id'] for v in d['votes']}
        valid_slots = set(
            poll.slots.filter(uuid__in=slot_ids).values_list('uuid', flat=True)
        )

        for vote_data in d['votes']:
            if vote_data['slot_id'] not in valid_slots:
                continue
            PollVote.objects.update_or_create(
                slot_id=vote_data['slot_id'],
                user=None,
                guest_name=d['guest_name'],
                defaults={
                    'choice': vote_data['choice'],
                    'guest_email': d.get('guest_email', ''),
                },
            )

        return Response(PollSerializer(poll, context={'request': request}).data)

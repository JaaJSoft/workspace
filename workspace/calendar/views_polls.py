import re
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.notifications.services import notify_many

from .models import Calendar, Event, EventMember, Poll, PollInvitee, PollSlot, PollVote
from .serializers_polls import (
    GuestVoteSubmitSerializer,
    PollCreateSerializer,
    PollFinalizeSerializer,
    PollInviteSerializer,
    PollListSerializer,
    PollSerializer,
    PollUpdateSerializer,
    VoteSubmitSerializer,
)

User = get_user_model()


def _poll_detail_queryset():
    """Base queryset for poll detail views with all prefetches to avoid N+1."""
    return Poll.objects.select_related('created_by').prefetch_related(
        Prefetch(
            'slots',
            queryset=PollSlot.objects.annotate(
                yes_count=Count('votes', filter=Q(votes__choice='yes')),
                maybe_count=Count('votes', filter=Q(votes__choice='maybe')),
            ).order_by('position', 'start'),
        ),
        'invitees__user',
    )


def _prefetch_poll_votes(poll):
    """Prefetch all votes for a poll and attach them to the poll object."""
    poll._prefetched_poll_votes = list(
        PollVote.objects.filter(slot__poll=poll).select_related('user')
    )
    return poll


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
            voted_poll_ids = (
                PollVote.objects
                .filter(user=request.user)
                .values_list('slot__poll_id', flat=True)
                .distinct()
            )
            invited_poll_ids = (
                PollInvitee.objects
                .filter(user=request.user)
                .values_list('poll_id', flat=True)
            )
            polls = Poll.objects.filter(
                Q(uuid__in=voted_poll_ids) | Q(uuid__in=invited_poll_ids)
            ).exclude(created_by=request.user)
        else:
            polls = Poll.objects.filter(created_by=request.user)

        if status_by != 'all':
            polls = polls.filter(status=status_by)

        polls = polls.select_related('created_by').annotate(
            _participant_count=Count('slots__votes', distinct=True),
        )

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

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
        return Response(
            PollSerializer(poll, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Calendar - Polls'])
class PollDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_poll(self, poll_id, user):
        return get_object_or_404(_poll_detail_queryset(), uuid=poll_id, created_by=user)

    @extend_schema(summary="Get poll detail", responses=PollSerializer)
    def get(self, request, poll_id):
        poll = get_object_or_404(_poll_detail_queryset(), uuid=poll_id)
        _prefetch_poll_votes(poll)
        return Response(PollSerializer(poll, context={'request': request}).data)

    @extend_schema(summary="Update poll", request=PollUpdateSerializer, responses=PollSerializer)
    def patch(self, request, poll_id):
        poll = self._get_poll(poll_id, request.user)
        ser = PollUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        d = ser.validated_data
        for field in ('title', 'description'):
            if field in d:
                setattr(poll, field, d[field])
        poll.save()

        if 'slots' in d:
            submitted_uuids = {
                slot_data['uuid']
                for slot_data in d['slots']
                if slot_data.get('uuid')
            }
            existing = {str(s.uuid): s for s in poll.slots.all()}

            # Delete removed slots (cascade deletes their votes)
            removed_uuids = set(existing.keys()) - submitted_uuids
            if removed_uuids:
                PollSlot.objects.filter(uuid__in=removed_uuids).delete()

            # Update kept slots + create new ones
            for i, slot_data in enumerate(d['slots']):
                slot_uuid = slot_data.get('uuid')
                if slot_uuid and slot_uuid in existing:
                    slot = existing[slot_uuid]
                    slot.start = slot_data['start']
                    slot.end = slot_data.get('end')
                    slot.position = i
                    slot.save()
                else:
                    PollSlot.objects.create(
                        poll=poll,
                        start=slot_data['start'],
                        end=slot_data.get('end'),
                        position=i,
                    )

            # Notify voters who had voted on removed slots
            if removed_uuids:
                voter_ids = (
                    PollVote.objects
                    .filter(slot__poll=poll, user__isnull=False)
                    .exclude(user=request.user)
                    .values_list('user_id', flat=True)
                    .distinct()
                )
                voters = list(User.objects.filter(id__in=voter_ids))
                if voters:
                    notify_many(
                        recipients=voters,
                        origin='calendar',
                        title=f'Poll updated: "{poll.title}"',
                        body='Time slots have been updated. Please review your votes.',
                        url=f'/calendar?poll={poll.pk}',
                        actor=request.user,
                    )

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
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

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
        return Response(PollSerializer(poll, context={'request': request}).data)


@extend_schema(tags=['Calendar - Polls'])
class PollFinalizeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Finalize poll", request=PollFinalizeSerializer, responses=PollSerializer)
    def post(self, request, poll_id):
        poll = get_object_or_404(
            Poll, uuid=poll_id, created_by=request.user, status=Poll.Status.OPEN,
        )
        # Note: we don't use _poll_detail_queryset() here because we modify the poll
        # and re-fetch at the end
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

        # Notify invitees about finalization
        invitee_users = list(
            User.objects.filter(poll_invitations__poll=poll)
            .exclude(id=request.user.id)
        )
        if invitee_users:
            notify_many(
                recipients=invitee_users,
                origin='calendar',
                title=f'Poll finalized: "{poll.title}"',
                body='A date has been chosen.',
                url=f'/calendar?event={event.pk}',
                actor=request.user,
            )

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
        return Response(PollSerializer(poll, context={'request': request}).data)


@extend_schema(tags=['Calendar - Polls'])
class PollInviteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Invite users to a poll", request=PollInviteSerializer, responses=PollSerializer)
    def post(self, request, poll_id):
        poll = get_object_or_404(
            Poll, uuid=poll_id, created_by=request.user, status=Poll.Status.OPEN,
        )
        ser = PollInviteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        user_ids = ser.validated_data['user_ids']
        # Exclude creator and already-invited users
        users = User.objects.filter(id__in=user_ids).exclude(id=request.user.id)
        invitees = [
            PollInvitee(poll=poll, user=user)
            for user in users
        ]
        PollInvitee.objects.bulk_create(invitees, ignore_conflicts=True)

        # Notify newly invited users
        newly_invited = list(users)
        if newly_invited:
            notify_many(
                recipients=newly_invited,
                origin='calendar',
                title=f'Poll invitation: "{poll.title}"',
                body=f'{request.user.username} invited you to vote on a poll.',
                url=f'/calendar?poll={poll.pk}',
                actor=request.user,
            )

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
        return Response(PollSerializer(poll, context={'request': request}).data)

    @extend_schema(summary="Remove invited users from a poll", request=PollInviteSerializer, responses=PollSerializer)
    def delete(self, request, poll_id):
        poll = get_object_or_404(
            Poll, uuid=poll_id, created_by=request.user, status=Poll.Status.OPEN,
        )
        ser = PollInviteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        user_ids = ser.validated_data['user_ids']

        PollInvitee.objects.filter(poll=poll, user_id__in=user_ids).delete()
        PollVote.objects.filter(slot__poll=poll, user_id__in=user_ids).delete()

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
        return Response(PollSerializer(poll, context={'request': request}).data)


# -- Public (shared link) views --

@extend_schema(tags=['Calendar - Polls (Public)'])
class SharedPollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Get poll via share link", responses=PollSerializer)
    def get(self, request, token):
        poll = get_object_or_404(_poll_detail_queryset(), share_token=token)
        _prefetch_poll_votes(poll)
        data = PollSerializer(poll, context={'request': request}).data

        # Restore guest's previous votes via voter_token
        voter_token = (
            request.query_params.get('voter_token', '')
            or request.COOKIES.get(f'poll_voter_{poll.share_token}', '')
        )
        if voter_token:
            my_votes = [
                v for v in poll._prefetched_poll_votes
                if v.voter_token == voter_token
            ]
            data['my_votes'] = {str(v.slot_id): v.choice for v in my_votes}
            if my_votes:
                data['my_guest_name'] = my_votes[0].guest_name
                data['my_guest_email'] = my_votes[0].guest_email

        return Response(data)


@extend_schema(tags=['Calendar - Polls (Public)'])
class SharedPollVoteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def _get_client_ip(self, request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    @extend_schema(summary="Submit guest votes", request=GuestVoteSubmitSerializer, responses=PollSerializer)
    def post(self, request, token):
        poll = get_object_or_404(Poll, share_token=token, status=Poll.Status.OPEN)

        # Rate limit: max 10 vote submissions per IP per hour
        ip = self._get_client_ip(request)
        rate_key = f'poll_vote_rate:{token}:{ip}'
        attempts = cache.get(rate_key, 0)
        if attempts >= 10:
            return Response(
                {'detail': 'Too many vote submissions. Please try again later.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(rate_key, attempts + 1, 3600)

        ser = GuestVoteSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        # Sanitize voter_token: must be a valid UUID or we generate one
        _uuid_re = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
        voter_token = d.get('voter_token', '').strip()
        if not voter_token or not _uuid_re.match(voter_token):
            voter_token = str(uuid.uuid4())

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
                voter_token=voter_token,
                defaults={
                    'choice': vote_data['choice'],
                    'guest_name': d['guest_name'],
                    'guest_email': d.get('guest_email', ''),
                },
            )

        poll = _poll_detail_queryset().get(uuid=poll.uuid)
        _prefetch_poll_votes(poll)
        resp_data = PollSerializer(poll, context={'request': request}).data
        resp_data['voter_token'] = voter_token
        response = Response(resp_data)
        # Use poll.share_token (DB-validated) instead of raw URL param
        cookie_name = f'poll_voter_{poll.share_token}'
        response.set_cookie(
            cookie_name,
            voter_token,
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite='Lax',
        )
        return response

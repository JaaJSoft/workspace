import orjson

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.calendar.models import Calendar, CalendarSubscription, Event, EventMember, Poll, PollInvitee, PollVote
from workspace.calendar.serializers import CalendarSerializer


def _ensure_default_calendar(user):
    """Create a default 'Personal' calendar if the user has none."""
    if not Calendar.objects.filter(owner=user).exists():
        Calendar.objects.create(
            owner=user,
            name='Personal',
            color='primary',
        )


@login_required
@ensure_csrf_cookie
def index(request):
    _ensure_default_calendar(request.user)

    owned = Calendar.objects.filter(owner=request.user).select_related('owner')
    sub_ids = CalendarSubscription.objects.filter(
        user=request.user,
    ).values_list('calendar_id', flat=True)
    subscribed = Calendar.objects.filter(uuid__in=sub_ids).select_related('owner')

    # Count open polls where user is invited but hasn't voted
    invited_poll_ids = PollInvitee.objects.filter(
        user=request.user,
        poll__status='open',
    ).values_list('poll_id', flat=True)
    voted_poll_ids = PollVote.objects.filter(
        user=request.user,
        slot__poll_id__in=invited_poll_ids,
    ).values_list('slot__poll_id', flat=True).distinct()
    poll_count = invited_poll_ids.exclude(poll_id__in=voted_poll_ids).count()

    return render(request, 'calendar/ui/index.html', {
        'owned_calendars': owned,
        'subscribed_calendars': subscribed,
        'calendars_json': orjson.dumps({
            'owned': CalendarSerializer(owned, many=True).data,
            'subscribed': CalendarSerializer(subscribed, many=True).data,
        }).decode(),
        'poll_count': poll_count,
    })


@login_required
def event_card(request, event_id):
    """Return a compact event card partial for popover display."""
    event = (
        Event.objects.filter(
            Q(owner=request.user) | Q(members__user=request.user, members__status__in=[
                EventMember.Status.ACCEPTED, EventMember.Status.PENDING,
            ]),
            uuid=event_id,
            is_cancelled=False,
        )
        .select_related('calendar', 'owner')
        .prefetch_related('members__user')
        .distinct()
        .first()
    )
    if not event:
        raise Http404
    attendees = list(event.members.select_related('user').exclude(
        status=EventMember.Status.DECLINED,
    )[:5])

    # For recurring occurrences, override start/end with the occurrence's actual times
    occ_start = None
    occ_end = None
    raw_start = request.GET.get('start')
    if raw_start:
        occ_start = parse_datetime(raw_start)
        if occ_start and event.start and event.end:
            duration = event.end - event.start
            occ_end = occ_start + duration

    return render(request, 'calendar/ui/partials/event_card.html', {
        'event': event,
        'attendees': attendees,
        'occ_start': occ_start,
        'occ_end': occ_end,
    })


@ensure_csrf_cookie
def polls_shared(request, token):
    poll = Poll.objects.filter(share_token=token).first()
    if not poll:
        raise Http404
    return render(request, 'calendar/ui/polls/shared.html', {
        'share_token': token,
    })

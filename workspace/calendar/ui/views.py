import orjson

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.calendar.models import Calendar, CalendarSubscription, Poll, PollInvitee, PollVote
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


@ensure_csrf_cookie
def polls_shared(request, token):
    poll = Poll.objects.filter(share_token=token).first()
    if not poll:
        raise Http404
    return render(request, 'calendar/ui/polls/shared.html', {
        'share_token': token,
    })

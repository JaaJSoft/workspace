import json

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.calendar.models import Calendar, CalendarSubscription, Poll
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

    poll_count = Poll.objects.filter(created_by=request.user, status='open').count()

    return render(request, 'calendar/ui/index.html', {
        'owned_calendars': owned,
        'subscribed_calendars': subscribed,
        'calendars_json': json.dumps({
            'owned': CalendarSerializer(owned, many=True).data,
            'subscribed': CalendarSerializer(subscribed, many=True).data,
        }),
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

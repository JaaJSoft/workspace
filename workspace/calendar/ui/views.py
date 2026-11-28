from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.utils.translation import pgettext
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.calendar.models import (
    Calendar,
    EventMember,
    Poll,
    PollInvitee,
    PollVote,
)
from workspace.calendar.queries import (
    reachable_calendars,
    visible_calendar_ids,
    visible_calendars,
)
from workspace.calendar.serializers import CalendarSerializer
from workspace.calendar.services.event_listing import find_occurrence
from workspace.common.prefetch import prefetchable
from workspace.users.services.settings import get_setting


def _ensure_default_calendar(user):
    """Create a default 'Personal' calendar if the user has none."""
    if not Calendar.objects.filter(owner=user).exists():
        Calendar.objects.create(
            owner=user,
            name=pgettext("default calendar name", "Personal"),
            color="primary",
        )


@prefetchable
@login_required
@ensure_csrf_cookie
def index(request):
    _ensure_default_calendar(request.user)

    owned, _subscribed = visible_calendars(request.user)

    # Count open polls where user is invited but hasn't voted
    invited_poll_ids = PollInvitee.objects.filter(
        user=request.user,
        poll__status="open",
    ).values_list("poll_id", flat=True)
    voted_poll_ids = (
        PollVote.objects.filter(
            user=request.user,
            slot__poll_id__in=invited_poll_ids,
        )
        .values_list("slot__poll_id", flat=True)
        .distinct()
    )
    poll_count = invited_poll_ids.exclude(poll_id__in=voted_poll_ids).count()

    # Server-render preferences so calendarApp() boots with correct view/firstDay/etc.
    # without an extra GET /api/v1/settings/calendar/preferences (which would force a
    # second FullCalendar render - and a second /api/v1/events fetch).
    prefs = get_setting(request.user, "calendar", "preferences", default={}) or {}

    return render(
        request,
        "calendar/ui/index.html",
        {
            "owned_calendars": owned,
            "calendars": CalendarSerializer(
                reachable_calendars(request.user).order_by("name", "uuid"),
                many=True,
                context={"request": request},
            ).data,
            "prefs": prefs,
            "poll_count": poll_count,
        },
    )


@login_required
def event_card(request, event_id):
    """Return a compact event card partial for popover display."""
    raw_start = request.GET.get("start")
    slot = None
    if raw_start:
        try:
            slot = parse_datetime(raw_start)
        except ValueError:
            slot = None
    occurrence = find_occurrence(request.user, event_id, slot)
    if occurrence is None or occurrence.row.is_cancelled:
        raise Http404
    # An occurrence changed on its own is a row with its own times and
    # invitees; one the rule gives is its series, at the slot asked for.
    event = occurrence.row
    # Both the attendee list and the viewer's membership below read from
    # the members prefetch; building fresh querysets here would bypass the
    # cache and issue one query each.
    members = list(event.members.all())
    attending = [m for m in members if m.status != EventMember.Status.DECLINED]
    attendees = attending[:5]

    occ_start = None
    occ_end = None
    if occurrence.changed is None and occurrence.original_start is not None:
        occ_start = occurrence.original_start
        if event.end:
            occ_end = occ_start + (event.end - event.start)

    is_owner = event.owner_id == request.user.id
    is_external = hasattr(event.calendar, "external_source")
    # An invitee reaches the event through its membership, not the calendar:
    # the organizer's calendar name and colour are not theirs to see.
    calendar = (
        event.calendar
        if event.calendar_id in visible_calendar_ids(request.user)
        else None
    )
    membership = next((m for m in members if m.user_id == request.user.id), None)
    invite_status = membership.status if membership and not is_owner else None

    return render(
        request,
        "calendar/ui/partials/event_card.html",
        {
            "event": event,
            "calendar": calendar,
            "attendees": attendees,
            "more_attendees": len(attending) - len(attendees),
            "is_owner": is_owner,
            "is_external": is_external,
            "invite_status": invite_status,
            "occ_start": occ_start,
            "occ_end": occ_end,
            # What the card's actions name the occurrence by.
            "occ_slot": occ_start.isoformat() if occ_start else "",
        },
    )


@ensure_csrf_cookie
def polls_shared(request, token):
    poll = Poll.objects.filter(share_token=token).first()
    if not poll:
        raise Http404
    return render(
        request,
        "calendar/ui/polls/shared.html",
        {
            "share_token": token,
        },
    )

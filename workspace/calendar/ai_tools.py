"""AI tools for the Calendar module."""

import json
import logging
import uuid as uuid_mod
from typing import Literal

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool
from workspace.common.datetimes import parse_local_datetime
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Empty means "leave as is"; this sentinel means "empty it out". Editing a
# field to blank and not editing it at all are different intents, and a
# function-calling schema has no way to say "absent".
CLEAR = "none"

SCOPE_DESCRIPTION = (
    "Which occurrences this applies to. 'this' = the single occurrence at "
    "original_start, 'future' = that occurrence and every later one, 'all' = "
    "the whole series. Ignored for non-recurring events. NEVER guess: ask the "
    "user which one they mean before calling."
)
ORIGINAL_START_DESCRIPTION = (
    "The occurrence's own start, as returned in original_start by "
    "list_upcoming_events. Required when scope is 'this' or 'future' on a "
    "recurring event, ignored otherwise."
)
CONFIRM_DESCRIPTION = (
    "Leave false on the first call: the user is shown the change and asked to "
    "confirm. Pass true only to repeat the identical call after they agreed."
)


class SearchEventsParams(BaseModel):
    query: str = Field(
        description="The search term to look for in event title, description "
        "and location, and in poll titles."
    )


class ListUpcomingEventsParams(BaseModel):
    days_ahead: int = Field(
        default=7, description="How many days ahead to look (default 7)."
    )
    limit: int = Field(
        default=20, description="Maximum number of events to return (default 20)."
    )


class CheckAvailabilityParams(BaseModel):
    start: str = Field(
        description="Start of the time range to check (ISO datetime, e.g. 2026-03-21T09:00)."
    )
    end: str = Field(
        description="End of the time range to check (ISO datetime, e.g. 2026-03-21T10:00)."
    )


class CreateEventParams(BaseModel):
    title: str = Field(max_length=255, description="The event title.")
    start: str = Field(
        description="Start datetime in ISO 8601 (e.g. 2026-07-05T14:00). "
        "Assumed to be in the user's timezone if no offset is given."
    )
    end: str = Field(
        default="",
        description="End datetime in ISO 8601. Optional.",
    )
    all_day: bool = Field(default=False, description="True for an all-day event.")
    location: str = Field(default="", max_length=255, description="Optional location.")
    description: str = Field(default="", description="Optional description or notes.")
    calendar: str = Field(
        default="",
        description="Name of the calendar to add the event to. If omitted, "
        "the user's first calendar is used.",
    )


class UpdateEventParams(BaseModel):
    event_id: uuid_mod.UUID = Field(
        description="UUID of the event, as returned by search_events, "
        "list_upcoming_events (event_id) or create_event."
    )
    scope: Literal["this", "future", "all"] = Field(description=SCOPE_DESCRIPTION)
    original_start: str = Field(default="", description=ORIGINAL_START_DESCRIPTION)
    title: str = Field(default="", max_length=255, description="New title. Optional.")
    start: str = Field(
        default="",
        description="New start datetime in ISO 8601 (e.g. 2026-07-05T14:00). "
        "Assumed to be in the user's timezone if no offset is given. Optional.",
    )
    end: str = Field(
        default="",
        description=f"New end datetime in ISO 8601, or '{CLEAR}' to drop the "
        "end time. Optional.",
    )
    location: str = Field(
        default="",
        max_length=255,
        description=f"New location, or '{CLEAR}' to clear it. Optional.",
    )
    description: str = Field(
        default="",
        description=f"New description, or '{CLEAR}' to clear it. Optional.",
    )
    attendees: list[str] = Field(
        default_factory=list,
        description="Exact usernames of the people invited to the event. "
        "Replaces the whole guest list, so include the ones already invited "
        f"unless you mean to remove them; ['{CLEAR}'] removes everyone. "
        "Omit to leave the guest list untouched.",
    )
    confirm: bool = Field(default=False, description=CONFIRM_DESCRIPTION)


class CancelEventParams(BaseModel):
    event_id: uuid_mod.UUID = Field(
        description="UUID of the event, as returned by search_events, "
        "list_upcoming_events (event_id) or create_event."
    )
    scope: Literal["this", "future", "all"] = Field(description=SCOPE_DESCRIPTION)
    original_start: str = Field(default="", description=ORIGINAL_START_DESCRIPTION)
    confirm: bool = Field(default=False, description=CONFIRM_DESCRIPTION)


class RespondToInvitationParams(BaseModel):
    event_id: uuid_mod.UUID = Field(
        description="UUID of the event the user was invited to."
    )
    response: Literal["accepted", "declined"] = Field(
        description="The user's answer to the invitation."
    )
    confirm: bool = Field(default=False, description=CONFIRM_DESCRIPTION)


class CreatePollParams(BaseModel):
    title: str = Field(max_length=255, description="What the poll is about.")
    slots: list[str] = Field(
        description="The candidate start datetimes in ISO 8601 (e.g. "
        "2026-07-05T14:00), 2 to 20 of them. Assumed to be in the user's "
        "timezone if no offset is given."
    )
    duration_minutes: int = Field(
        default=60, description="How long each candidate slot lasts (default 60)."
    )
    description: str = Field(default="", description="Optional extra context.")
    invitees: list[str] = Field(
        default_factory=list,
        description="Exact usernames to invite to vote. Optional; the poll can "
        "also be shared by link afterwards.",
    )


class GetPollResultsParams(BaseModel):
    poll_id: uuid_mod.UUID = Field(
        description="UUID of the poll, as returned by search_events or create_poll."
    )


def _writable_event(user, event_id):
    """Return ``(event, error)`` for an event *user* is allowed to write.

    Exactly one side is set. Reads go through ``visible_events_q`` so an
    event the user cannot see is indistinguishable from one that does not
    exist, and the write check refuses events on external calendars — an
    edit there is reverted by the next feed sync, after the tool has already
    told the user it worked.
    """
    from workspace.calendar.models import Event
    from workspace.calendar.queries import visible_events_q
    from workspace.calendar.services.event_scope import EventScopeError, assert_writable

    event = (
        Event.objects.filter(visible_events_q(user), uuid=event_id)
        .select_related("calendar")
        .first()
    )
    if event is None:
        return None, "Error: no event with that id, or you cannot see it."
    try:
        assert_writable(event, user)
    except EventScopeError as exc:
        return None, f"Error: {exc.detail}"
    return event, None


def _resolve_occurrence(event, scope, raw, user_tz):
    """Return ``(original_start, error)`` for a scoped edit of *event*.

    ``None`` with no error means the scope does not need one (a whole-series
    edit, or a non-recurring event where scope is moot).
    """
    from datetime import timedelta

    from workspace.calendar.recurrence import occurrences_in_range

    if scope == "all" or not event.is_recurring:
        return None, None
    if not raw.strip():
        return None, (
            f"Error: original_start is required for scope={scope}. Take it from "
            "the occurrence's original_start in list_upcoming_events."
        )
    occurrence = parse_local_datetime(raw.strip(), user_tz)
    if occurrence is None:
        return None, (
            f'Error: could not parse original_start "{raw}". '
            "Use ISO format like 2026-07-05T14:00"
        )
    # An instant that is not on the series grid would materialize an
    # exception nothing ever matches: the write succeeds and changes nothing
    # the user can see.
    window_end = occurrence + timedelta(seconds=1)
    if not any(
        occ == occurrence for occ in occurrences_in_range(event, occurrence, window_end)
    ):
        return None, (
            f"Error: {occurrence.isoformat()} is not an occurrence of this series. "
            "Take original_start from list_upcoming_events rather than computing it."
        )
    return occurrence, None


def _resolve_usernames(names):
    """Return ``(user_ids, error)`` for a list of exact usernames.

    An unknown name is reported rather than dropped: silently inviting four
    people out of five is worse than inviting nobody.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    wanted = [n.strip() for n in names if n.strip()]
    if not wanted:
        return [], None

    User = get_user_model()
    lookup = Q()
    for name in wanted:
        lookup |= Q(username__iexact=name)
    found = {
        u.username.lower(): u.id for u in User.objects.filter(lookup, is_active=True)
    }
    unknown = sorted({n for n in wanted if n.lower() not in found})
    if unknown:
        return None, (
            f"Error: no active user named {', '.join(unknown)}. "
            "Use search_users to find the exact username."
        )
    return sorted({found[n.lower()] for n in wanted}), None


def _describe_scope(event, scope):
    """Human phrasing of what a scoped write will touch."""
    if not event.is_recurring:
        return ""
    return {
        "this": " (this occurrence only)",
        "future": " (this occurrence and all later ones)",
        "all": " (the whole recurring series)",
    }[scope]


class CalendarToolProvider(ToolProvider):
    @tool(
        badge_icon="🔍",
        badge_label="Searched events",
        badge_running_label="Searching events",
        detail_key="query",
        params=SearchEventsParams,
        concurrent=True,
    )
    def search_events(self, args, user, bot, conversation_id, context):
        """Search your calendar events by title, description or location, and \
scheduling polls by title. Returns up to 20 matches with title, date, calendar, \
and location. Call this when the user asks about upcoming events, meetings, or \
scheduling polls."""
        query = args.query.strip()
        if not query:
            return "Error: query is required"

        from workspace.calendar.models import Poll
        from workspace.calendar.services.event_search import search_events_qs

        # Cap the combined event+poll payload at the documented 20 matches,
        # giving events priority and letting polls fill the remaining budget.
        events = list(search_events_qs(user, query).select_related("calendar")[:20])

        poll_limit = min(10, 20 - len(events))
        polls = Poll.objects.filter(created_by=user, title__icontains=query).order_by(
            "-created_at"
        )[:poll_limit]

        results = []
        for e in events:
            entry = {
                "type": "event",
                "uuid": str(e.uuid),
                "title": e.title,
                "calendar": e.calendar.name if e.calendar else "",
                "start": e.start.strftime("%Y-%m-%d %H:%M") if e.start else "",
                "end": e.end.strftime("%Y-%m-%d %H:%M") if e.end else "",
                "all_day": e.all_day,
            }
            if e.location:
                entry["location"] = e.location
            results.append(entry)

        for p in polls:
            results.append(
                {
                    "type": "poll",
                    "uuid": str(p.uuid),
                    "title": p.title,
                    "status": p.status,
                    "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
                }
            )

        if not results:
            return f'No events or polls found matching "{query}".'
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="\U0001f4c5",
        badge_label="Checked availability",
        badge_running_label="Checking availability",
        params=CheckAvailabilityParams,
        concurrent=True,
    )
    def check_availability(self, args, user, bot, conversation_id, context):
        """Check whether the user is available (free) during a given time range. \
Call this when the user asks if they are free, available, or have any events during a specific period."""
        from django.db.models import Q

        from workspace.calendar.models import Event
        from workspace.calendar.queries import visible_calendar_ids
        from workspace.calendar.recurrence import occurrences_in_range
        from workspace.users.services.settings import get_user_timezone

        user_tz = get_user_timezone(user)

        start = parse_local_datetime(args.start.strip(), user_tz)
        if start is None:
            return f'Error: could not parse start datetime "{args.start}". Use ISO format like 2026-03-21T09:00'
        end = parse_local_datetime(args.end.strip(), user_tz)
        if end is None:
            return f'Error: could not parse end datetime "{args.end}". Use ISO format like 2026-03-21T10:00'

        if end <= start:
            return "Error: end must be after start"

        # All calendars visible to the user (owned + subscribed)
        cal_ids = visible_calendar_ids(user)
        if not cal_ids:
            return json.dumps(
                {
                    "available": True,
                    "events": [],
                    "message": "No calendars found — user is free.",
                }
            )

        # Non-recurring events overlapping the range
        time_overlap = Q(start__lt=end) & (
            Q(end__gt=start) | Q(end__isnull=True, start__gte=start)
        )
        non_recurring_qs = (
            Event.objects.filter(
                calendar_id__in=cal_ids,
                is_recurring=False,
                recurrence_parent__isnull=True,
                is_cancelled=False,
            )
            .filter(time_overlap)
            .select_related("calendar")
            .order_by("start")
        )

        conflicts = []
        for ev in non_recurring_qs:
            conflicts.append(
                {
                    "title": ev.title,
                    "start": ev.start.isoformat(),
                    "end": ev.end.isoformat() if ev.end else None,
                    "all_day": ev.all_day,
                    "calendar": ev.calendar.name,
                }
            )

        # Recurring masters — expand occurrences in range
        masters_qs = Event.objects.filter(
            calendar_id__in=cal_ids,
            is_recurring=True,
            recurrence_parent__isnull=True,
            is_cancelled=False,
        ).select_related("calendar")

        # Build exception index for cancelled occurrences
        master_ids = [m.uuid for m in masters_qs]
        exc_cancelled = set()
        if master_ids:
            for exc in Event.objects.filter(
                recurrence_parent_id__in=master_ids,
                is_cancelled=True,
                original_start__isnull=False,
            ):
                exc_cancelled.add(
                    (str(exc.recurrence_parent_id), exc.original_start.isoformat())
                )

            # Materialized non-cancelled exceptions overlapping range
            for exc in (
                Event.objects.filter(
                    recurrence_parent_id__in=master_ids,
                    is_cancelled=False,
                )
                .filter(time_overlap)
                .select_related("calendar")
            ):
                conflicts.append(
                    {
                        "title": exc.title,
                        "start": exc.start.isoformat(),
                        "end": exc.end.isoformat() if exc.end else None,
                        "all_day": exc.all_day,
                        "calendar": exc.calendar.name,
                    }
                )

        for master in masters_qs:
            for occ_start in occurrences_in_range(master, start, end):
                key = (str(master.uuid), occ_start.isoformat())
                if key in exc_cancelled:
                    continue
                duration = (master.end - master.start) if master.end else None
                occ_end = (occ_start + duration) if duration else None
                conflicts.append(
                    {
                        "title": master.title,
                        "start": occ_start.isoformat(),
                        "end": occ_end.isoformat() if occ_end else None,
                        "all_day": master.all_day,
                        "calendar": master.calendar.name,
                    }
                )

        conflicts.sort(key=lambda e: e["start"])

        if not conflicts:
            start_str = start.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
            end_str = end.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
            return json.dumps(
                {
                    "available": True,
                    "events": [],
                    "message": f"User is free from {start_str} to {end_str}.",
                }
            )

        return json.dumps(
            {
                "available": False,
                "events": conflicts,
                "message": f"{len(conflicts)} event(s) found in this time range.",
            },
            ensure_ascii=False,
        )

    @tool(
        badge_icon="📅",
        badge_label="Listed calendars",
        badge_running_label="Listing calendars",
        concurrent=True,
    )
    def list_calendars(self, args, user, bot, conversation_id, context):
        """List the user's own calendars (the ones you can add events to). \
Call this before create_event when the user names a specific calendar, \
or when the user asks which calendars they have."""
        from workspace.calendar.queries import visible_calendars

        owned, _ = visible_calendars(user)
        calendars = [{"name": c.name, "color": c.color} for c in owned]
        if not calendars:
            return "You have no calendars yet."
        return json.dumps(calendars, ensure_ascii=False)

    @tool(
        badge_icon="📅",
        badge_label="Checked agenda",
        badge_running_label="Checking agenda",
        params=ListUpcomingEventsParams,
        concurrent=True,
    )
    def list_upcoming_events(self, args, user, bot, conversation_id, context):
        """List the user's upcoming events, including recurring occurrences. \
Call this when the user asks what is coming up, what they have this week, or \
about their next events. For a keyword lookup use search_events; to check \
whether a specific time range is free use check_availability. Each entry \
carries the event_id (and, for a recurring occurrence, its original_start) \
that update_event and cancel_event take."""
        from datetime import timedelta

        from dateutil.parser import parse as parse_dt
        from django.utils import timezone

        from workspace.calendar.queries import visible_calendars
        from workspace.calendar.upcoming import get_upcoming_page
        from workspace.users.services.settings import get_user_timezone

        now = timezone.now()
        limit = max(1, min(args.limit, 100))
        days_ahead = max(1, args.days_ahead)
        cutoff = now + timedelta(days=days_ahead)

        events, _ = get_upcoming_page(user, after=now, limit=limit)

        owned, subscribed = visible_calendars(user)
        cal_names = {str(c.uuid): c.name for c in list(owned) + list(subscribed)}

        user_tz = get_user_timezone(user)
        results = []
        for e in events:
            start_dt = parse_dt(e["start"])
            if start_dt > cutoff:
                continue
            start_local = start_dt.astimezone(user_tz)
            end_local = parse_dt(e["end"]).astimezone(user_tz) if e.get("end") else None
            entry = {
                # A virtual occurrence's own uuid is a synthetic
                # "<master>:<start>" pair no endpoint accepts; the master is
                # what the edit tools address.
                "event_id": e.get("master_event_id") or e["uuid"],
                "title": e["title"],
                "start": start_local.strftime("%Y-%m-%d %H:%M"),
                "end": end_local.strftime("%Y-%m-%d %H:%M") if end_local else "",
                "all_day": e["all_day"],
                "location": e.get("location", ""),
                "calendar": cal_names.get(e.get("calendar_id"), ""),
            }
            if e.get("is_recurring"):
                entry["recurring"] = True
                entry["original_start"] = e.get("original_start") or e["start"]
            results.append(entry)

        if not results:
            return f"No events in the next {days_ahead} day(s)."
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="➕",
        badge_label="Added to calendar",
        badge_running_label="Adding to calendar",
        detail_key="title",
        params=CreateEventParams,
    )
    def create_event(self, args, user, bot, conversation_id, context):
        """Create a new event in the user's calendar. \
Call this when the user asks to add, create, schedule, or book an event, \
meeting, or appointment. Creates a single (non-recurring) event. If the user \
names a calendar, pass it in `calendar`; call list_calendars first if unsure."""
        from django.utils import timezone as dj_tz

        from workspace.calendar.models import Calendar, Event
        from workspace.calendar.queries import visible_calendars
        from workspace.users.services.settings import get_user_timezone

        title = args.title.strip()
        if not title:
            return "Error: title is required"

        user_tz = get_user_timezone(user)

        start = parse_local_datetime(args.start.strip(), user_tz)
        if start is None:
            return (
                f'Error: could not parse start datetime "{args.start}". '
                "Use ISO format like 2026-07-05T14:00"
            )

        end = None
        if args.end.strip():
            end = parse_local_datetime(args.end.strip(), user_tz)
            if end is None:
                return (
                    f'Error: could not parse end datetime "{args.end}". '
                    "Use ISO format like 2026-07-05T15:00"
                )
            if end <= start:
                return "Error: end must be after start"

        if not args.all_day and start <= dj_tz.now():
            return "Error: start must be in the future"

        owned, _ = visible_calendars(user)
        owned_list = list(owned)
        requested = args.calendar.strip()
        if requested:
            calendar = next(
                (c for c in owned_list if c.name.lower() == requested.lower()),
                None,
            )
            if calendar is None:
                names = ", ".join(c.name for c in owned_list) or "(none)"
                return (
                    f'Error: no calendar named "{requested}". Your calendars: {names}'
                )
        elif owned_list:
            calendar = owned_list[0]
        else:
            calendar, _ = Calendar.objects.get_or_create(owner=user, name="Perso")

        event = Event.objects.create(
            calendar=calendar,
            owner=user,
            title=title,
            description=args.description.strip(),
            start=start,
            end=end,
            all_day=args.all_day,
            location=args.location.strip(),
            source=Event.Source.MANUAL,
        )
        logger.info(
            "AI created event %s in calendar %s for %s",
            scrub(title),
            scrub(calendar.name),
            scrub(user.username),
        )
        start_local = start.astimezone(user_tz)
        return (
            f'Created event "{title}" in calendar "{calendar.name}" '
            f"on {start_local.strftime('%Y-%m-%d %H:%M')} (id: {event.uuid})."
        )

    @tool(
        badge_icon="✏️",
        badge_label="Updated event",
        badge_running_label="Updating event",
        detail_key="title",
        params=UpdateEventParams,
    )
    def update_event(self, args, user, bot, conversation_id, context):
        """Change an existing event: its title, time, location, description or \
guest list. Call this when the user wants to move, rename, re-locate or re-staff \
something already in their calendar — never re-create the event with create_event, \
which leaves the old one behind. You must pass `scope`; on a recurring event ask \
the user whether they mean this occurrence, this one and the following, or the \
whole series before choosing. Only the owner can edit, and events from an external \
(subscribed ICS) calendar cannot be edited at all."""
        from workspace.ai.services.confirmation import request_confirmation
        from workspace.calendar.services import event_scope
        from workspace.users.services.settings import get_user_timezone

        event, err = _writable_event(user, args.event_id)
        if err:
            return err

        user_tz = get_user_timezone(user)
        original_start, err = _resolve_occurrence(
            event, args.scope, args.original_start, user_tz
        )
        if err:
            return err

        data = {}
        if args.title.strip():
            data["title"] = args.title.strip()
        if args.start.strip():
            start = parse_local_datetime(args.start.strip(), user_tz)
            if start is None:
                return (
                    f'Error: could not parse start datetime "{args.start}". '
                    "Use ISO format like 2026-07-05T14:00"
                )
            data["start"] = start
        if args.end.strip():
            if args.end.strip().lower() == CLEAR:
                data["end"] = None
            else:
                end = parse_local_datetime(args.end.strip(), user_tz)
                if end is None:
                    return (
                        f'Error: could not parse end datetime "{args.end}". '
                        "Use ISO format like 2026-07-05T15:00"
                    )
                data["end"] = end
        if args.location.strip():
            data["location"] = (
                "" if args.location.strip().lower() == CLEAR else args.location.strip()
            )
        if args.description.strip():
            data["description"] = (
                ""
                if args.description.strip().lower() == CLEAR
                else args.description.strip()
            )
        if args.attendees:
            if [a.strip().lower() for a in args.attendees] == [CLEAR]:
                data["member_ids"] = []
            else:
                member_ids, err = _resolve_usernames(args.attendees)
                if err:
                    return err
                data["member_ids"] = member_ids

        if not data:
            return "Error: nothing to change — pass at least one field to update."

        # The new end must beat the new start, and either side may be the one
        # already stored. A scoped edit writes a row anchored on
        # original_start, not on the master's own start, and rebuilds its end
        # from the series duration — so on that path the master's stored
        # start and end both say nothing about the row being written.
        new_start = data.get("start", original_start or event.start)
        if "end" in data:
            new_end = data["end"]
        elif args.scope == "all" or not event.is_recurring:
            new_end = event.end
        else:
            new_end = None
        if new_end and new_start and new_end <= new_start:
            return "Error: end must be after start"

        # A guest-list change is externally visible whether or not the event
        # recurs: sync_members notifies everyone added and everyone removed,
        # and no confirmation afterwards un-sends those.
        touches_guests = "member_ids" in data
        if (event.is_recurring or touches_guests) and not args.confirm:
            return request_confirmation(
                context,
                f'Update "{event.title}"{_describe_scope(event, args.scope)}?',
            )

        try:
            written = event_scope.update_event(
                event, data, user, scope=args.scope, original_start=original_start
            )
        except event_scope.EventScopeError as exc:
            return f"Error: {exc.detail}"

        logger.info(
            "AI updated event %s for %s (scope=%s)",
            scrub(written.title),
            scrub(user.username),
            args.scope,
        )
        start_local = written.start.astimezone(user_tz)
        return (
            f'Updated "{written.title}"{_describe_scope(event, args.scope)} — '
            f"now on {start_local.strftime('%Y-%m-%d %H:%M')} (id: {written.uuid})."
        )

    @tool(
        badge_icon="🗑️",
        badge_label="Cancelled event",
        badge_running_label="Cancelling event",
        params=CancelEventParams,
    )
    def cancel_event(self, args, user, bot, conversation_id, context):
        """Cancel an event: remove it from the calendar and tell the guests. \
Call this when the user says a meeting is off, cancelled, or should be deleted. \
You must pass `scope`; on a recurring event ask the user whether they mean to skip \
this one occurrence, end the series from here on, or delete it entirely — deleting \
a whole weekly meeting when they wanted to skip one Monday cannot be undone. Only \
the owner can cancel, and events from an external (subscribed ICS) calendar cannot \
be cancelled at all."""
        from workspace.ai.services.confirmation import request_confirmation
        from workspace.calendar.services import event_scope
        from workspace.users.services.settings import get_user_timezone

        event, err = _writable_event(user, args.event_id)
        if err:
            return err

        original_start, err = _resolve_occurrence(
            event, args.scope, args.original_start, get_user_timezone(user)
        )
        if err:
            return err

        if not args.confirm:
            return request_confirmation(
                context,
                f'Cancel "{event.title}"{_describe_scope(event, args.scope)}?',
            )

        title = event.title
        try:
            event_scope.cancel_event(
                event, user, scope=args.scope, original_start=original_start
            )
        except event_scope.EventScopeError as exc:
            return f"Error: {exc.detail}"

        logger.info(
            "AI cancelled event %s for %s (scope=%s)",
            scrub(title),
            scrub(user.username),
            args.scope,
        )
        if args.scope == "this" and original_start:
            return f'Cancelled the occurrence of "{title}" on {original_start.date()}.'
        if args.scope == "future" and original_start:
            return f'Ended the "{title}" series from {original_start.date()} onwards.'
        return f'Cancelled "{title}". Any guests have been notified.'

    @tool(
        badge_icon="✉️",
        badge_label="Answered invitation",
        badge_running_label="Answering invitation",
        params=RespondToInvitationParams,
    )
    def respond_to_invitation(self, args, user, bot, conversation_id, context):
        """Accept or decline an invitation the user has received. Call this when \
the user says yes or no to a meeting someone else organised. The answer leaves the \
workspace — the organiser is notified, and for an invitation that arrived by email \
an iCalendar reply is sent back to them — so it always needs the user's explicit \
confirmation first."""
        from workspace.ai.services.confirmation import request_confirmation
        from workspace.calendar.models import Event
        from workspace.calendar.queries import visible_events_q
        from workspace.calendar.services import invitations

        event = Event.objects.filter(visible_events_q(user), uuid=args.event_id).first()
        if event is None:
            return "Error: no event with that id, or you cannot see it."

        verb = "Accept" if args.response == "accepted" else "Decline"
        if not args.confirm:
            return request_confirmation(
                context,
                f'{verb} the invitation to "{event.title}"? '
                "The organiser will be told.",
            )

        try:
            invitations.respond_to_invitation(event.uuid, user, args.response)
        except invitations.NotInvitedError:
            return f'Error: you are not on the guest list of "{event.title}".'

        logger.info(
            "AI answered invitation %s as %s for %s",
            scrub(event.title),
            args.response,
            scrub(user.username),
        )
        return f'Invitation to "{event.title}" {args.response}. The organiser was notified.'

    @tool(
        badge_icon="🗳️",
        badge_label="Created poll",
        badge_running_label="Creating poll",
        detail_key="title",
        params=CreatePollParams,
    )
    def create_poll(self, args, user, bot, conversation_id, context):
        """Create a scheduling poll so invitees can vote on which of several \
candidate slots suits them. Call this when the user wants to FIND a time rather \
than book one — "when can we all meet", "propose a few slots to the team". Use \
check_availability first to pick candidate slots the user is actually free for. \
Once the votes are in, get_poll_results reports the winner and the user turns it \
into an event."""
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.db import transaction
        from django.utils import timezone as dj_tz

        from workspace.calendar.models import Poll, PollInvitee, PollSlot
        from workspace.notifications.services.notifications import notify_many
        from workspace.users.services.settings import get_user_timezone

        title = args.title.strip()
        if not title:
            return "Error: title is required"

        user_tz = get_user_timezone(user)
        duration = max(1, min(args.duration_minutes, 24 * 60))

        starts = []
        for raw in args.slots:
            parsed = parse_local_datetime(raw.strip(), user_tz)
            if parsed is None:
                return (
                    f'Error: could not parse slot "{raw}". '
                    "Use ISO format like 2026-07-05T14:00"
                )
            starts.append(parsed)

        starts = sorted(set(starts))
        if len(starts) < 2:
            return "Error: a poll needs at least 2 distinct candidate slots."
        if len(starts) > 20:
            return "Error: a poll takes at most 20 candidate slots."
        if starts[0] <= dj_tz.now():
            return "Error: candidate slots must be in the future"

        invitee_ids, err = _resolve_usernames(args.invitees)
        if err:
            return err
        invitees = [uid for uid in invitee_ids if uid != user.id]

        with transaction.atomic():
            poll = Poll.objects.create(
                title=title,
                description=args.description.strip(),
                created_by=user,
            )
            PollSlot.objects.bulk_create(
                [
                    PollSlot(
                        poll=poll,
                        start=start,
                        end=start + timedelta(minutes=duration),
                        position=i,
                    )
                    for i, start in enumerate(starts)
                ]
            )
            if invitees:
                PollInvitee.objects.bulk_create(
                    [PollInvitee(poll=poll, user_id=uid) for uid in invitees],
                    ignore_conflicts=True,
                )

        if invitees:
            recipients = list(get_user_model().objects.filter(id__in=invitees))
            notify_many(
                recipients=recipients,
                origin="calendar",
                title=f'Poll invitation: "{poll.title}"',
                body=f"{user.username} invited you to vote on a poll.",
                url=f"/calendar?poll={poll.pk}",
                actor=user,
                source=poll,
            )

        logger.info(
            "AI created poll %s with %d slots for %s",
            scrub(title),
            len(starts),
            scrub(user.username),
        )
        listed = ", ".join(
            s.astimezone(user_tz).strftime("%Y-%m-%d %H:%M") for s in starts
        )
        return (
            f'Created poll "{title}" with {len(starts)} slots ({listed}) '
            f"(id: {poll.uuid})."
        )

    @tool(
        badge_icon="📊",
        badge_label="Read poll results",
        badge_running_label="Reading poll results",
        params=GetPollResultsParams,
        concurrent=True,
    )
    def get_poll_results(self, args, user, bot, conversation_id, context):
        """Report where a scheduling poll stands: every candidate slot with who \
voted yes, maybe or no, and which slot is currently winning. Call this when the \
user asks how a poll is going, who has answered, or which slot works best."""
        from django.db.models import Count, Q

        from workspace.calendar.models import Poll, PollInvitee, PollSlot, PollVote
        from workspace.users.services.settings import get_user_timezone

        poll = (
            Poll.objects.filter(uuid=args.poll_id).select_related("created_by").first()
        )
        if poll is None:
            return "Error: no poll with that id."
        is_participant = (
            poll.created_by_id == user.id
            or PollInvitee.objects.filter(poll=poll, user=user).exists()
            or PollVote.objects.filter(slot__poll=poll, user=user).exists()
        )
        if not is_participant:
            return "Error: no poll with that id, or you have no access to it."

        user_tz = get_user_timezone(user)
        slots = (
            PollSlot.objects.filter(poll=poll)
            .annotate(
                yes_count=Count("votes", filter=Q(votes__choice="yes")),
                maybe_count=Count("votes", filter=Q(votes__choice="maybe")),
                no_count=Count("votes", filter=Q(votes__choice="no")),
            )
            .order_by("position", "start")
        )

        voters_by_slot = {}
        for vote in PollVote.objects.filter(slot__poll=poll).select_related("user"):
            who = vote.user.username if vote.user else (vote.guest_name or "guest")
            voters_by_slot.setdefault(vote.slot_id, {}).setdefault(
                vote.choice, []
            ).append(who)

        entries = []
        for slot in slots:
            voters = voters_by_slot.get(slot.uuid, {})
            entries.append(
                {
                    "start": slot.start.astimezone(user_tz).strftime("%Y-%m-%d %H:%M"),
                    "end": slot.end.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
                    if slot.end
                    else "",
                    "yes": slot.yes_count,
                    "maybe": slot.maybe_count,
                    "no": slot.no_count,
                    "voters": voters,
                }
            )

        payload = {
            "title": poll.title,
            "status": poll.status,
            "created_by": poll.created_by.username,
            "invitees": sorted(
                PollInvitee.objects.filter(poll=poll).values_list(
                    "user__username", flat=True
                )
            ),
            "slots": entries,
        }
        # Ranked the same way the UI does: a maybe is worth less than a yes
        # but still beats silence, so it breaks ties instead of being ignored.
        best = max(entries, key=lambda e: (e["yes"], e["maybe"]), default=None)
        if best and (best["yes"] or best["maybe"]):
            payload["leading_slot"] = best["start"]
        if not any(e["yes"] or e["maybe"] or e["no"] for e in entries):
            payload["note"] = "Nobody has voted yet."
        return json.dumps(payload, ensure_ascii=False)

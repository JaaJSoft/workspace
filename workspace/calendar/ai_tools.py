"""AI tools for the Calendar module."""

import json
import logging

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


class SearchEventsParams(BaseModel):
    query: str = Field(
        description="The search term to look for in event and poll titles."
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
    title: str = Field(description="The event title.")
    start: str = Field(
        description="Start datetime in ISO 8601 (e.g. 2026-07-05T14:00). "
        "Assumed to be in the user's timezone if no offset is given."
    )
    end: str = Field(
        default="",
        description="End datetime in ISO 8601. Optional.",
    )
    all_day: bool = Field(default=False, description="True for an all-day event.")
    location: str = Field(default="", description="Optional location.")
    description: str = Field(default="", description="Optional description or notes.")
    calendar: str = Field(
        default="",
        description="Name of the calendar to add the event to. If omitted, "
        "the user's first calendar is used.",
    )


class CalendarToolProvider(ToolProvider):
    @tool(
        badge_icon="🔍",
        badge_label="Searched events",
        detail_key="query",
        params=SearchEventsParams,
    )
    def search_events(self, args, user, bot, conversation_id, context):
        """Search through your calendar events and scheduling polls by title. \
Returns up to 20 matches with title, date, calendar, and location. \
Call this when the user asks about upcoming events, meetings, or scheduling polls."""
        query = args.query.strip()
        if not query:
            return "Error: query is required"

        from workspace.calendar.models import Event, Poll
        from workspace.calendar.queries import visible_events_q

        # Events the user can see
        events = (
            Event.objects.filter(
                visible_events_q(user),
                title__icontains=query,
                recurrence_parent__isnull=True,
                is_cancelled=False,
            )
            .select_related("calendar")
            .distinct()
            .order_by("-start")[:20]
        )

        # Polls created by user
        polls = Poll.objects.filter(created_by=user, title__icontains=query).order_by(
            "-created_at"
        )[:10]

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
        params=CheckAvailabilityParams,
    )
    def check_availability(self, args, user, bot, conversation_id, context):
        """Check whether the user is available (free) during a given time range. \
Call this when the user asks if they are free, available, or have any events during a specific period."""
        from datetime import datetime

        from django.db.models import Q

        from workspace.calendar.models import Event
        from workspace.calendar.queries import visible_calendar_ids
        from workspace.calendar.recurrence import _build_rrule
        from workspace.users.services.settings import get_user_timezone

        user_tz = get_user_timezone(user)

        try:
            start = datetime.fromisoformat(args.start.strip())
        except ValueError:
            return f'Error: could not parse start datetime "{args.start}". Use ISO format like 2026-03-21T09:00'
        try:
            end = datetime.fromisoformat(args.end.strip())
        except ValueError:
            return f'Error: could not parse end datetime "{args.end}". Use ISO format like 2026-03-21T10:00'

        if start.tzinfo is None:
            start = start.replace(tzinfo=user_tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=user_tz)

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
                recurrence_frequency__isnull=True,
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
            recurrence_frequency__isnull=False,
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
            for occ_start in _build_rrule(master, start, end):
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

    @tool(badge_icon="📅", badge_label="Listed calendars")
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
        params=ListUpcomingEventsParams,
    )
    def list_upcoming_events(self, args, user, bot, conversation_id, context):
        """List the user's upcoming events, including recurring occurrences. \
Call this when the user asks what is coming up, what they have this week, or \
about their next events. For a keyword lookup use search_events; to check \
whether a specific time range is free use check_availability."""
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
            results.append(
                {
                    "title": e["title"],
                    "start": start_local.strftime("%Y-%m-%d %H:%M"),
                    "end": end_local.strftime("%Y-%m-%d %H:%M") if end_local else "",
                    "all_day": e["all_day"],
                    "location": e.get("location", ""),
                    "calendar": cal_names.get(e.get("calendar_id"), ""),
                }
            )

        if not results:
            return f"No events in the next {days_ahead} day(s)."
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="➕",
        badge_label="Added to calendar",
        detail_key="title",
        params=CreateEventParams,
    )
    def create_event(self, args, user, bot, conversation_id, context):
        """Create a new event in the user's calendar. \
Call this when the user asks to add, create, schedule, or book an event, \
meeting, or appointment. Creates a single (non-recurring) event. If the user \
names a calendar, pass it in `calendar`; call list_calendars first if unsure."""
        from datetime import datetime

        from django.utils import timezone as dj_tz

        from workspace.calendar.models import Calendar, Event
        from workspace.calendar.queries import visible_calendars
        from workspace.users.services.settings import get_user_timezone

        title = args.title.strip()
        if not title:
            return "Error: title is required"

        user_tz = get_user_timezone(user)

        try:
            start = datetime.fromisoformat(args.start.strip())
        except ValueError:
            return (
                f'Error: could not parse start datetime "{args.start}". '
                "Use ISO format like 2026-07-05T14:00"
            )
        if start.tzinfo is None:
            start = start.replace(tzinfo=user_tz)

        end = None
        if args.end.strip():
            try:
                end = datetime.fromisoformat(args.end.strip())
            except ValueError:
                return (
                    f'Error: could not parse end datetime "{args.end}". '
                    "Use ISO format like 2026-07-05T15:00"
                )
            if end.tzinfo is None:
                end = end.replace(tzinfo=user_tz)
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
            calendar = Calendar.objects.create(name="Perso", owner=user)

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

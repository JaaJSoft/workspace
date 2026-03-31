"""AI tools for the Calendar module."""
import json

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool


class SearchEventsParams(BaseModel):
    query: str = Field(description="The search term to look for in event and poll titles.")


class CheckAvailabilityParams(BaseModel):
    start: str = Field(description="Start of the time range to check (ISO datetime, e.g. 2026-03-21T09:00).")
    end: str = Field(description="End of the time range to check (ISO datetime, e.g. 2026-03-21T10:00).")


class CalendarToolProvider(ToolProvider):

    @tool(badge_icon='🔍', badge_label='Searched events', detail_key='query', params=SearchEventsParams)
    def search_events(self, args, user, bot, conversation_id, context):
        """Search through your calendar events and scheduling polls by title. \
Returns up to 20 matches with title, date, calendar, and location. \
Call this when the user asks about upcoming events, meetings, or scheduling polls."""
        query = args.query.strip()
        if not query:
            return 'Error: query is required'

        from workspace.calendar.models import Event, Poll
        from workspace.calendar.queries import visible_events_q

        # Events the user can see
        events = (
            Event.objects
            .filter(
                visible_events_q(user),
                title__icontains=query,
                recurrence_parent__isnull=True,
                is_cancelled=False,
            )
            .select_related('calendar')
            .distinct()
            .order_by('-start')[:20]
        )

        # Polls created by user
        polls = (
            Poll.objects
            .filter(created_by=user, title__icontains=query)
            .order_by('-created_at')[:10]
        )

        results = []
        for e in events:
            entry = {
                'type': 'event',
                'uuid': str(e.uuid),
                'title': e.title,
                'calendar': e.calendar.name if e.calendar else '',
                'start': e.start.strftime('%Y-%m-%d %H:%M') if e.start else '',
                'end': e.end.strftime('%Y-%m-%d %H:%M') if e.end else '',
                'all_day': e.all_day,
            }
            if e.location:
                entry['location'] = e.location
            results.append(entry)

        for p in polls:
            results.append({
                'type': 'poll',
                'uuid': str(p.uuid),
                'title': p.title,
                'status': p.status,
                'created_at': p.created_at.strftime('%Y-%m-%d %H:%M'),
            })

        if not results:
            return f'No events or polls found matching "{query}".'
        return json.dumps(results, ensure_ascii=False)

    @tool(badge_icon='\U0001f4c5', badge_label='Checked availability', params=CheckAvailabilityParams)
    def check_availability(self, args, user, bot, conversation_id, context):
        """Check whether the user is available (free) during a given time range. \
Call this when the user asks if they are free, available, or have any events during a specific period."""
        from datetime import datetime
        from django.db.models import Q
        from workspace.calendar.models import Event
        from workspace.calendar.queries import visible_calendar_ids
        from workspace.calendar.recurrence import _build_rrule
        from workspace.users.settings_service import get_user_timezone

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
            return 'Error: end must be after start'

        # All calendars visible to the user (owned + subscribed)
        cal_ids = visible_calendar_ids(user)
        if not cal_ids:
            return json.dumps({'available': True, 'events': [], 'message': 'No calendars found — user is free.'})

        # Non-recurring events overlapping the range
        time_overlap = Q(start__lt=end) & (Q(end__gt=start) | Q(end__isnull=True, start__gte=start))
        non_recurring_qs = (
            Event.objects.filter(
                calendar_id__in=cal_ids,
                recurrence_frequency__isnull=True,
                recurrence_parent__isnull=True,
                is_cancelled=False,
            )
            .filter(time_overlap)
            .select_related('calendar')
            .order_by('start')
        )

        conflicts = []
        for ev in non_recurring_qs:
            conflicts.append({
                'title': ev.title,
                'start': ev.start.isoformat(),
                'end': ev.end.isoformat() if ev.end else None,
                'all_day': ev.all_day,
                'calendar': ev.calendar.name,
            })

        # Recurring masters — expand occurrences in range
        masters_qs = (
            Event.objects.filter(
                calendar_id__in=cal_ids,
                recurrence_frequency__isnull=False,
                recurrence_parent__isnull=True,
                is_cancelled=False,
            )
            .select_related('calendar')
        )

        # Build exception index for cancelled occurrences
        master_ids = [m.uuid for m in masters_qs]
        exc_cancelled = set()
        if master_ids:
            for exc in Event.objects.filter(
                recurrence_parent_id__in=master_ids,
                is_cancelled=True,
                original_start__isnull=False,
            ):
                exc_cancelled.add((str(exc.recurrence_parent_id), exc.original_start.isoformat()))

            # Materialized non-cancelled exceptions overlapping range
            for exc in Event.objects.filter(
                recurrence_parent_id__in=master_ids,
                is_cancelled=False,
            ).filter(time_overlap).select_related('calendar'):
                conflicts.append({
                    'title': exc.title,
                    'start': exc.start.isoformat(),
                    'end': exc.end.isoformat() if exc.end else None,
                    'all_day': exc.all_day,
                    'calendar': exc.calendar.name,
                })

        for master in masters_qs:
            for occ_start in _build_rrule(master, start, end):
                key = (str(master.uuid), occ_start.isoformat())
                if key in exc_cancelled:
                    continue
                duration = (master.end - master.start) if master.end else None
                occ_end = (occ_start + duration) if duration else None
                conflicts.append({
                    'title': master.title,
                    'start': occ_start.isoformat(),
                    'end': occ_end.isoformat() if occ_end else None,
                    'all_day': master.all_day,
                    'calendar': master.calendar.name,
                })

        conflicts.sort(key=lambda e: e['start'])

        if not conflicts:
            start_str = start.astimezone(user_tz).strftime('%Y-%m-%d %H:%M')
            end_str = end.astimezone(user_tz).strftime('%Y-%m-%d %H:%M')
            return json.dumps({
                'available': True,
                'events': [],
                'message': f'User is free from {start_str} to {end_str}.',
            })

        return json.dumps({
            'available': False,
            'events': conflicts,
            'message': f'{len(conflicts)} event(s) found in this time range.',
        }, ensure_ascii=False)

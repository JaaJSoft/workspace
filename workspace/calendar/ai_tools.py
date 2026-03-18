"""AI tools for the Calendar module."""
import json

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool


class SearchEventsParams(BaseModel):
    query: str = Field(description="The search term to look for in event and poll titles.")


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

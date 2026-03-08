"""AI tools for the Calendar module."""
import json

from workspace.ai.tool_registry import Param, ToolProvider, tool


class CalendarToolProvider(ToolProvider):

    @tool(badge_icon='🔍', badge_label='Searched events', detail_key='query', params={
        'query': Param('The search term to look for in event and poll titles.'),
    })
    def search_events(self, args, user, bot, conversation_id, context):
        """Search through your calendar events and scheduling polls by title. \
Returns up to 20 matches with title, date, calendar, and location. \
Call this when the user asks about upcoming events, meetings, or scheduling polls."""
        query = args.get('query', '').strip()
        if not query:
            return 'Error: query is required'

        from django.db.models import Q
        from workspace.calendar.models import (
            Calendar, CalendarSubscription, Event, EventMember, Poll,
        )

        # Events the user can see
        owned_cal_ids = Calendar.objects.filter(owner=user).values_list('uuid', flat=True)
        sub_cal_ids = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
        member_event_ids = EventMember.objects.filter(user=user).values_list('event_id', flat=True)

        events = (
            Event.objects
            .filter(
                Q(calendar_id__in=owned_cal_ids)
                | Q(calendar_id__in=sub_cal_ids)
                | Q(uuid__in=member_event_ids),
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

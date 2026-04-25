from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from workspace.core.activity_registry import ActivityProvider


class CalendarActivityProvider(ActivityProvider):

    def _visibility_filter(self, user_id, viewer_id):
        """Restrict to events visible to viewer (owned, subscribed or event membership)."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from workspace.calendar.queries import visible_events_q
        return visible_events_q(viewer_id)

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.calendar.models import Event

        qs = Event.objects.filter(
            is_cancelled=False,
            updated_at__date__gte=date_from,
            updated_at__date__lte=date_to,
        )
        if user_id is not None:
            qs = qs.filter(
                owner_id=user_id,
                calendar__external_source__isnull=True,
            )
        qs = qs.filter(self._visibility_filter(user_id, viewer_id))

        rows = qs.annotate(day=TruncDate('updated_at')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')

        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.calendar.models import Event

        qs = Event.objects.filter(
            is_cancelled=False,
        )
        if user_id is not None:
            qs = qs.filter(
                owner_id=user_id,
                calendar__external_source__isnull=True,
            )
        qs = qs.filter(
            self._visibility_filter(user_id, viewer_id),
        ).select_related(
            'owner', 'calendar__external_source',
        ).order_by('-updated_at')[offset:offset + limit]

        events = []
        for evt in qs:
            is_external = hasattr(evt.calendar, 'external_source')

            if is_external:
                label = 'Event synced'
                actor = None
            else:
                is_new = abs((evt.created_at - evt.updated_at).total_seconds()) < 2
                label = 'Event created' if is_new else 'Event updated'
                actor = {
                    'id': evt.owner_id,
                    'username': evt.owner.username,
                    'full_name': evt.owner.get_full_name(),
                }

            events.append({
                'icon': 'calendar',
                'label': label,
                'description': evt.title,
                'timestamp': evt.updated_at,
                'url': f'/calendar?event={evt.pk}',
                'actor': actor,
            })
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.calendar.models import Event

        now = timezone.now()
        base = Event.objects.filter(is_cancelled=False)
        if user_id is not None:
            base = base.filter(
                owner_id=user_id,
                calendar__external_source__isnull=True,
            )

        upcoming = base.filter(
            start__gte=now,
        ).filter(self._visibility_filter(user_id, viewer_id)).count()

        total = base.filter(
            self._visibility_filter(user_id, viewer_id),
        ).count()

        return {
            'upcoming_events': upcoming,
            'total_events': total,
        }

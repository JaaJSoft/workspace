from django.apps import AppConfig


class CalendarConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.calendar'
    label = 'calendar'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, PendingActionProviderInfo, SearchProviderInfo, registry
        from workspace.calendar.search import search_events

        registry.register(ModuleInfo(
            name='Calendar',
            slug='calendar',
            description='Plan and manage events, meetings and invitations.',
            icon='calendar',
            color='accent',
            url='/calendar',
            order=20,
        ))

        registry.register_search_provider(SearchProviderInfo(
            slug='calendar',
            module_slug='calendar',
            search_fn=search_events,
        ))

        from workspace.calendar.search import search_polls

        registry.register_search_provider(SearchProviderInfo(
            slug='calendar_polls',
            module_slug='calendar',
            search_fn=search_polls,
        ))

        def _calendar_pending_actions(user):
            from datetime import timedelta
            from django.db.models import Q
            from django.utils import timezone
            from workspace.calendar.models import Event, EventMember
            now = timezone.now()
            return Event.objects.filter(
                Q(owner=user) | Q(members__user=user, members__status__in=[
                    EventMember.Status.ACCEPTED, EventMember.Status.PENDING,
                ]),
                start__gte=now,
                start__lte=now + timedelta(days=7),
            ).distinct().count()

        registry.register_pending_action_provider(PendingActionProviderInfo(
            module_slug='calendar',
            pending_action_fn=_calendar_pending_actions,
        ))

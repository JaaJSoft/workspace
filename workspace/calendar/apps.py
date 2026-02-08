from django.apps import AppConfig


class CalendarConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.calendar'
    label = 'calendar'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, SearchProviderInfo, registry
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

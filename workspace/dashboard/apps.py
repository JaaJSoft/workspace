from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.dashboard'

    def ready(self):
        from workspace.common.module_registry import ModuleInfo, registry

        registry.register(ModuleInfo(
            name='Dashboard',
            slug='dashboard',
            description='Overview of your workspace.',
            icon='home',
            color='secondary',
            url='/',
            order=0,
        ))

        planned_modules = [
            ModuleInfo(
                name='Emails',
                slug='emails',
                description='Send and receive emails.',
                icon='mail',
                color='secondary',
                url=None,
                active=False,
                order=20,
            ),
            ModuleInfo(
                name='Notes',
                slug='notes',
                description='Write and collaborate on documents.',
                icon='notebook-pen',
                color='accent',
                url=None,
                active=False,
                order=30,
            ),
            ModuleInfo(
                name='Calendar',
                slug='calendar',
                description='Schedule events and reminders.',
                icon='calendar',
                color='info',
                url=None,
                active=False,
                order=40,
            ),
            ModuleInfo(
                name='Tasks',
                slug='tasks',
                description='Track projects and to-dos.',
                icon='check-square',
                color='warning',
                url=None,
                active=False,
                order=50,
            ),
            ModuleInfo(
                name='Polls',
                slug='polls',
                description='Create surveys and collect responses.',
                icon='bar-chart-3',
                color='error',
                url=None,
                active=False,
                order=60,
            ),
        ]
        for module in planned_modules:
            registry.register(module)

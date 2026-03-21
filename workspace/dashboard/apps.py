from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.dashboard'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

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
                name='Contacts',
                slug='contacts',
                description='Manage contacts and interactions.',
                icon='contact',
                color='info',
                url=None,
                active=False,
                order=60,
            ),
            ModuleInfo(
                name='Bookmarks',
                slug='bookmarks',
                description='Save and organize links.',
                icon='bookmark',
                color='primary',
                url=None,
                active=False,
                order=70,
            ),
            ModuleInfo(
                name='Passwords',
                slug='passwords',
                description='Encrypted password vault.',
                icon='lock',
                color='error',
                url=None,
                active=False,
                order=80,
            ),
        ]
        for module in planned_modules:
            registry.register(module)

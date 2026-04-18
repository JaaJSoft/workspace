from django.apps import AppConfig


class NotesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.notes'

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, SearchProviderInfo, registry
        from workspace.core.activity_registry import ActivityProviderInfo, activity_registry
        from workspace.notes.search import search_notes
        from workspace.notes.activity import NotesActivityProvider

        registry.register(ModuleInfo(
            name='Notes',
            slug='notes',
            description='Write and organize markdown notes.',
            icon='notebook-pen',
            color='success',
            url='/notes',
            order=30,
        ))

        registry.register_search_provider(SearchProviderInfo(
            slug='notes',
            module_slug='notes',
            search_fn=search_notes,
        ))

        registry.register_commands([
            CommandInfo(
                name='Notes', keywords=['notes', 'markdown', 'writing'],
                icon='notebook-pen', color='success', url='/notes',
                kind='navigate', module_slug='notes', order=30,
            ),
            CommandInfo(
                name='New note', keywords=['new note', 'create note'],
                icon='file-plus', color='success', url='/notes?action=new',
                kind='action', module_slug='notes', order=31,
            ),
        ])

        activity_registry.register(ActivityProviderInfo(
            slug='notes',
            label='Notes',
            icon='notebook-pen',
            color='success',
            provider_cls=NotesActivityProvider,
        ))

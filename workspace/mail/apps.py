from django.apps import AppConfig


class MailConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.mail'
    label = 'mail'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, SearchProviderInfo, registry
        from workspace.mail.search import search_mail

        registry.register(ModuleInfo(
            name='Mail',
            slug='mail',
            description='Read and send emails from external mail accounts.',
            icon='mail',
            color='warning',
            url='/mail',
            order=25,
        ))

        registry.register_search_provider(SearchProviderInfo(
            slug='mail',
            module_slug='mail',
            search_fn=search_mail,
        ))

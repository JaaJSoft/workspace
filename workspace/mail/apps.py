from django.apps import AppConfig


class MailConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.mail'
    label = 'mail'

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, PendingActionProviderInfo, SearchProviderInfo, registry
        from workspace.mail.search import search_contacts, search_mail

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

        registry.register_search_provider(SearchProviderInfo(
            slug='mail-contacts',
            module_slug='mail',
            search_fn=search_contacts,
        ))

        def _mail_pending_actions(user):
            from workspace.mail.models import MailMessage
            return MailMessage.objects.filter(
                account__owner=user,
                is_read=False,
                deleted_at__isnull=True,
            ).count()

        registry.register_pending_action_provider(PendingActionProviderInfo(
            module_slug='mail',
            pending_action_fn=_mail_pending_actions,
        ))

        registry.register_commands([
            CommandInfo(
                name='Mail', keywords=['mail', 'email', 'inbox'],
                icon='mail', color='warning', url='/mail',
                kind='navigate', module_slug='mail', order=25,
            ),
            CommandInfo(
                name='New email', keywords=['new email', 'compose', 'send'],
                icon='mail-plus', color='warning', url='/mail?compose=',
                kind='action', module_slug='mail', order=26,
            ),
        ])

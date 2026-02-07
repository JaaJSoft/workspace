from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.chat'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(ModuleInfo(
            name='Chat',
            slug='chat',
            description='Real-time messaging with direct and group conversations.',
            icon='message-circle',
            color='info',
            url='/chat',
            order=15,
        ))

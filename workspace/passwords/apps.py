from django.apps import AppConfig


class PasswordsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.passwords'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(ModuleInfo(
            name='Passwords',
            slug='passwords',
            description='Encrypted password vault.',
            icon='key-round',
            color='warning',
            url='/passwords',
            order=50,
        ))

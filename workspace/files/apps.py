from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.files'

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(ModuleInfo(
            name='Files',
            slug='files',
            description='Store, organize and share files.',
            icon='hard-drive',
            color='primary',
            url='/files',
            order=10,
        ))

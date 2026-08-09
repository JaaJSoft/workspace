from django.apps import AppConfig


class PasswordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.passwords"

    def ready(self):
        from workspace.core.module_registry import ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name="Passwords",
                slug="passwords",
                description="Store passwords in an end-to-end encrypted vault.",
                icon="key-round",
                color="error",
                url="/passwords",
                order=40,
                preview=True,
            )
        )

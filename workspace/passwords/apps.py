from django.apps import AppConfig


class PasswordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.passwords"

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, registry

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

        registry.register_commands(
            [
                CommandInfo(
                    name="Passwords",
                    keywords=["passwords", "vault", "secrets"],
                    icon="key-round",
                    color="error",
                    url="/passwords",
                    kind="navigate",
                    module_slug="passwords",
                    order=40,
                ),
            ]
        )

from django.apps import AppConfig


class VaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.vault"

    def ready(self):
        from workspace.core.module_registry import CommandInfo, ModuleInfo, registry

        registry.register(
            ModuleInfo(
                name="Vault",
                slug="vault",
                description="Store passwords in an end-to-end encrypted vault.",
                icon="key-round",
                color="error",
                url="/vault",
                order=40,
                preview=True,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Vault",
                    keywords=["passwords", "vault", "secrets"],
                    icon="key-round",
                    color="error",
                    url="/vault",
                    kind="navigate",
                    module_slug="vault",
                    order=40,
                ),
            ]
        )

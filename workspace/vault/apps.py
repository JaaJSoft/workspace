from django.apps import AppConfig


class VaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.vault"

    def ready(self):
        # The entry proxies are Django models, so the app registry has to hold
        # them whatever else imported them: an app registry that depends on
        # import order makes `makemigrations --check` answer differently in CI
        # than it does here. The actions register on import too, and are
        # pulled in here rather than lazily so a broken import fails the boot
        # instead of leaving a worker answering "no actions" forever.
        from workspace.core.module_registry import CommandInfo, ModuleInfo, registry
        from workspace.vault import types  # noqa: F401
        from workspace.vault.actions import entry as entry_actions  # noqa: F401

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

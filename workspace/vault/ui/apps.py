from django.apps import AppConfig


class VaultUiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.vault.ui"
    label = "vault_ui"
    verbose_name = "Vault UI"

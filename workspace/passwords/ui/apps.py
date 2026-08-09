from django.apps import AppConfig


class PasswordsUiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.passwords.ui"
    label = "passwords_ui"
    verbose_name = "Passwords UI"

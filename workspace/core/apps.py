from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.core"

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import m2m_changed

        from workspace.core.signals import on_user_groups_changed

        User = get_user_model()
        m2m_changed.connect(
            on_user_groups_changed,
            sender=User.groups.through,
            dispatch_uid="module_access_user_groups_changed",
        )

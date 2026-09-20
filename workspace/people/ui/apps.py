from django.apps import AppConfig


class PeopleUiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.people.ui"
    label = "people_ui"
    verbose_name = "People UI"

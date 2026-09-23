from django.apps import AppConfig


class PhotosUiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.photos.ui"
    label = "photos_ui"
    verbose_name = "Photos UI"

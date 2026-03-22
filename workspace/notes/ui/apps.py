from django.apps import AppConfig


class NotesUiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspace.notes.ui'
    label = 'notes_ui'
    verbose_name = 'Notes UI'

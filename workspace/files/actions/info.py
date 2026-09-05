from . import ActionRegistry
from .base import ActionCategory, BaseFileAction


@ActionRegistry.register
class AnalyzeStorageAction(BaseFileAction):
    id = "analyze_storage"
    label = "Analyze storage"
    icon = "chart-pie"
    category = ActionCategory.INFO
    node_types = ("folder",)

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        return permission is not None


@ActionRegistry.register
class PropertiesAction(BaseFileAction):
    id = "properties"
    label = "Properties"
    icon = "info"
    category = ActionCategory.INFO
    node_types = ("file", "folder")
    keyboard_shortcut = "Ctrl+I"

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        return permission is not None

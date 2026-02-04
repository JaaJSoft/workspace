from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class PropertiesAction(BaseAction):
    id = 'properties'
    label = 'Properties'
    icon = 'info'
    category = ActionCategory.INFO
    node_types = ('file', 'folder')
    keyboard_shortcut = 'Ctrl+I'

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner or share_permission is not None

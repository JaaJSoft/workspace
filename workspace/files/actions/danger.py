from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class DeleteAction(BaseAction):
    id = 'delete'
    label = 'Delete'
    icon = 'trash-2'
    category = ActionCategory.DANGER
    node_types = ('file', 'folder')
    keyboard_shortcut = 'Del'
    css_class = 'text-error'
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        # Cannot delete a root group folder
        if file_obj.group_id and file_obj.parent_id is None:
            return False
        return is_owner

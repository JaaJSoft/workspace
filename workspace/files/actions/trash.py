from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class RestoreAction(BaseAction):
    id = 'restore'
    label = 'Restore'
    icon = 'rotate-ccw'
    category = ActionCategory.TRASH
    node_types = ('file', 'folder')
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is None:
            return False
        return is_owner


@ActionRegistry.register
class PurgeAction(BaseAction):
    id = 'purge'
    label = 'Delete permanently'
    icon = 'trash-2'
    category = ActionCategory.TRASH
    node_types = ('file', 'folder')
    css_class = 'text-error'
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is None:
            return False
        return is_owner

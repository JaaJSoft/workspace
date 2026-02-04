from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class DownloadAction(BaseAction):
    id = 'download'
    label = 'Download'
    icon = 'download'
    category = ActionCategory.TRANSFER
    node_types = ('file', 'folder')
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner or share_permission is not None

    def get_label(self, file_obj):
        if file_obj.node_type == 'folder':
            return 'Download as ZIP'
        return 'Download'


@ActionRegistry.register
class CopyLinkAction(BaseAction):
    id = 'copy_link'
    label = 'Copy link'
    icon = 'link'
    category = ActionCategory.TRANSFER
    node_types = ('file',)

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner or share_permission is not None

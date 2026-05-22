from workspace.files.services import FilePermission
from workspace.files.services.extract import ZIP_MIME_TYPES
from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class ExtractAction(BaseAction):
    id = 'extract'
    label = 'Extract archive'
    icon = 'archive-restore'
    category = ActionCategory.EDIT
    node_types = ('file',)

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        if file_obj.mime_type not in ZIP_MIME_TYPES:
            return False
        return permission is not None and permission >= FilePermission.EDIT

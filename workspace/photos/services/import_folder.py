"""The folder photos imported from the Photos page land in.

Stored as a folder uuid in the `photos` / `import_folder` user setting. Until
the user picks one, imports go to a root folder named "Images", created on
the first import (never on a page view: a user who never imports should not
find a folder they did not ask for).
"""

from django.db import transaction

from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.users.services.settings import get_setting, set_setting

MODULE = "photos"
IMPORT_FOLDER = "import_folder"
DEFAULT_NAME = "Images"


def _writable_folders(user):
    """The folders *user* can add files to: their own and their groups'."""
    return (
        FileService.user_files_qs(user) | FileService.user_group_files_qs(user)
    ).filter(node_type=File.NodeType.FOLDER)


def writable_folder(user, folder_uuid):
    """The folder *folder_uuid* names if *user* may import into it, else None."""
    folder_uuid = parse_uuid_or_none(folder_uuid)
    if folder_uuid is None:
        return None
    return _writable_folders(user).filter(uuid=folder_uuid).first()


def _default_folder(user):
    return (
        FileService.user_files_qs(user)
        .filter(node_type=File.NodeType.FOLDER, parent__isnull=True, name=DEFAULT_NAME)
        .first()
    )


def import_folder(user):
    """The folder imports go to, or None while the default one does not exist.

    Side-effect free: a chosen folder that was trashed or left behind falls
    back to the default one.
    """
    chosen = writable_folder(user, get_setting(user, MODULE, IMPORT_FOLDER))
    return chosen or _default_folder(user)


@transaction.atomic
def ensure_import_folder(user):
    """The folder imports go to, creating the default one when missing."""
    folder = import_folder(user)
    if folder is None:
        folder = FileService.create_folder(
            user, DEFAULT_NAME, icon="image", color="primary", acting_user=user
        )
    if get_setting(user, MODULE, IMPORT_FOLDER) != str(folder.uuid):
        set_setting(user, MODULE, IMPORT_FOLDER, str(folder.uuid))
    return folder


def import_folder_data(folder):
    """What the page shows of the import folder; None is the default one, not created yet."""
    if folder is None:
        return {"uuid": None, "name": DEFAULT_NAME, "path": DEFAULT_NAME, "group": None}
    return {
        "uuid": str(folder.uuid),
        "name": folder.name,
        "path": folder.path or folder.name,
        "group": folder.group.name if folder.group_id else None,
    }


def choose_import_folder(user, folder):
    set_setting(user, MODULE, IMPORT_FOLDER, str(folder.uuid))

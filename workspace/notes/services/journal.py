from workspace.files.models import File
from workspace.users.services.settings import get_setting


def is_journal_note(user, file_obj) -> bool:
    """Return True iff file_obj is a non-deleted file directly inside the user's Journal folder.

    The Journal folder's UUID is stored per-user in ``notes.preferences.journalFolderUuid``.
    If that pref is missing or empty, no file qualifies — callers should not fall back to
    ``_ensure_default_folders`` here (this helper is hot-path and side-effect free).
    """
    if file_obj is None:
        return False
    if getattr(file_obj, 'node_type', None) != File.NodeType.FILE:
        return False
    if getattr(file_obj, 'deleted_at', None) is not None:
        return False
    if getattr(file_obj, 'parent_id', None) is None:
        return False

    prefs = get_setting(user, 'notes', 'preferences', default={}) or {}
    journal_uuid = prefs.get('journalFolderUuid')
    if not journal_uuid:
        return False

    return str(file_obj.parent_id) == str(journal_uuid)

from workspace.files.models import File
from workspace.users.services.settings import get_setting

_UNRESOLVED = object()


def journal_folder_id(user):
    """The UUID string of the user's Journal folder, or None when it is unset.

    Stored per user in ``notes.preferences.journalFolderUuid``.
    """
    prefs = get_setting(user, "notes", "preferences", default={}) or {}
    return prefs.get("journalFolderUuid") or None


def is_journal_note(user, file_obj, journal_folder=_UNRESOLVED) -> bool:
    """Return True iff file_obj is a non-deleted file directly inside the user's Journal folder.

    ``journal_folder`` is the user's ``journal_folder_id``, for a caller
    checking many files at once; it is read from the settings otherwise.
    If the pref is missing or empty, no file qualifies - callers should not
    fall back to ``_ensure_default_folders`` here (this helper is hot-path
    and side-effect free).
    """
    if file_obj is None:
        return False
    if getattr(file_obj, "node_type", None) != File.NodeType.FILE:
        return False
    if getattr(file_obj, "deleted_at", None) is not None:
        return False
    if getattr(file_obj, "parent_id", None) is None:
        return False

    if journal_folder is _UNRESOLVED:
        journal_folder = journal_folder_id(user)
    if not journal_folder:
        return False

    return str(file_obj.parent_id) == str(journal_folder)

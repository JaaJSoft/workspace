"""The one rule every write path shares: an active lock fences out everyone
but its holder.

``File.locked_by`` / ``lock_expires_at`` are the single lock the whole app
reads - the in-app editors take it through ``/api/v1/files/<uuid>/lock``, WOPI
carries it in ``services/wopi/locks.py``, and WebDAV mirrors its own tokens
onto it in ``webdav/locks.py``. Every path that overwrites, renames, moves or
deletes a file asks this module the same question, so a writer that respects
the lock in one protocol cannot be overwritten through another.
"""


def conflicting_lock(file_obj, user):
    """Return the lock holder fencing *user* out of *file_obj*, or ``None``.

    ``None`` covers the three cases a write may proceed under: no lock, an
    expired one, and a lock the caller holds themselves.
    """
    if file_obj is None or not file_obj.is_locked():
        return None
    if user is not None and file_obj.locked_by_id == user.pk:
        return None
    return file_obj.locked_by

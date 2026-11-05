"""Which files a user may preview, and in what state.

A preview (files/ui/viewers.py, ``render_file_preview``) shows a file inside
something else: a note that embeds it, a list. The note and the file have
their own permissions, and the reader of the note is not necessarily allowed
to open what it embeds - the author can, they put it there. So the answer is
always for the user asking, through the same permission check as every other
file read, and a file they may not reach is simply absent: the answer does not
tell it apart from a file that does not exist.
"""

from __future__ import annotations

from ..models import File
from ..sensitivity import with_sensitivity
from . import FileService
from .scanning.policy import is_blocked, with_scan

OK = "ok"
# In the trash, previewed only by who may still restore it: its name, nothing else.
TRASHED = "trashed"
# Blocked by the malware scanner: its name, nothing else.
QUARANTINED = "quarantined"

THUMBNAIL_SIZE = 512


def file_version(file_obj):
    """A token that changes whenever the file's bytes do, so a replaced file
    is fetched again rather than served from the browser's memory."""
    if file_obj.content_hash:
        return file_obj.content_hash[:16]
    return str(int(file_obj.updated_at.timestamp() * 1000))


def content_url(file_obj):
    return f"/api/v1/files/{file_obj.uuid}/content?v={file_version(file_obj)}"


def thumbnail_url(file_obj):
    if not file_obj.has_thumbnail:
        return None
    return (
        f"/api/v1/files/{file_obj.uuid}/thumbnail"
        f"?size={THUMBNAIL_SIZE}&v={file_version(file_obj)}"
    )


def previewable_files(user, uuids):
    """Return ``{uuid: (state, file)}`` for the files of *uuids* *user* may
    preview. ``file`` carries its ``sensitive_level``; a file absent from the
    answer is one *user* may not reach, or one that does not exist."""
    files = list(
        with_scan(with_sensitivity(File.objects.filter(uuid__in=set(uuids)))).order_by()
    )
    permissions = FileService.get_permissions_bulk(user, files)
    found = {}
    for file_obj in files:
        if permissions.get(file_obj.pk) is None:
            continue
        if file_obj.deleted_at is not None:
            state = TRASHED
        elif is_blocked(file_obj):
            state = QUARANTINED
        else:
            state = OK
        found[file_obj.uuid] = (state, file_obj)
    return found

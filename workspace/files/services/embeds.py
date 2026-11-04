"""What a file embedded in a note shows to the person reading the note.

A note embeds a file as ``![name](/api/v1/files/<uuid>/content)``: a reference,
never a copy, so the note always shows the file as it is now. The note and
the file have their own permissions, and a reader of the note is not
necessarily allowed to open what it embeds - the author can, they put it there.
``resolve_embeds`` answers for the reader, through the same permission check
as every other file read, and tells the editor what to draw in place of each
embed: the file inline, or a placeholder that gives nothing of it away.
"""

from __future__ import annotations

from ..models import File
from ..sensitivity import with_sensitivity
from . import FileService
from .filetype import get_icon, get_viewer, get_viewer_by_slug
from .scanning.policy import is_blocked, with_scan

# The viewers whose output an embed reproduces inline; any other file is shown
# as a card that links to it.
INLINE_KINDS = frozenset({"image", "pdf", "audio", "video"})

# Matches the batch cap of the endpoint: a note embedding more files than this
# resolves them over several requests.
MAX_EMBEDS = 200

# The one answer for a file the reader cannot reach, whatever the reason: it
# does not exist, it was purged, or the reader has no right to it. Telling
# these apart would reveal whether a UUID names a file.
UNAVAILABLE = {"status": "unavailable"}

THUMBNAIL_SIZE = 512


def _kind(file_obj):
    """How an embed of *file_obj* is drawn: the slug of the viewer the Files
    app would open it with when the editor can reproduce it inline, ``note``
    for a note, ``folder`` for a folder and ``file`` for anything else."""
    if file_obj.node_type == File.NodeType.FOLDER:
        return "folder"
    viewer = get_viewer_by_slug(file_obj.viewer) or get_viewer(
        file_obj.type or "", file_obj.name or ""
    )
    slug = getattr(viewer, "slug", "")
    if slug in INLINE_KINDS:
        return slug
    if slug == "markdown":
        return "note"
    return "file"


def _version(file_obj):
    """A token that changes whenever the file's bytes do, so a replaced file
    is fetched again rather than served from the browser's memory."""
    if file_obj.content_hash:
        return file_obj.content_hash[:16]
    return str(int(file_obj.updated_at.timestamp() * 1000))


def _available(file_obj):
    base = f"/api/v1/files/{file_obj.uuid}"
    is_file = file_obj.node_type == File.NodeType.FILE
    version = _version(file_obj)
    return {
        "status": "ok",
        "name": file_obj.name,
        "kind": _kind(file_obj),
        "icon": get_icon(file_obj.type or "") if is_file else file_obj.icon or "folder",
        "mime_type": file_obj.mime_type or "",
        "size": file_obj.size,
        "content_url": f"{base}/content?v={version}" if is_file else None,
        "download_url": f"{base}/download" if is_file else None,
        "thumbnail_url": (
            f"{base}/thumbnail?size={THUMBNAIL_SIZE}&v={version}"
            if is_file and file_obj.has_thumbnail
            else None
        ),
        "sensitive": file_obj.sensitive_level or 0,
    }


def resolve_embeds(user, uuids):
    """Return ``{uuid: answer}`` for every UUID in *uuids*, as *user* sees it.

    An answer is a dict with a ``status``:

    - ``ok``: the file, with what the editor needs to draw it (``kind``,
      ``name``, the content, download and thumbnail URLs, its sensitivity
      level for the blur).
    - ``trashed``: the file is in the trash and the reader may still restore
      it; only its ``name`` is given.
    - ``quarantined``: the malware scanner blocked it; only its ``name``.
    - ``unavailable``: anything else - see ``UNAVAILABLE``.
    """
    uuids = set(uuids)
    files = list(
        with_scan(with_sensitivity(File.objects.filter(uuid__in=uuids))).order_by()
    )
    permissions = FileService.get_permissions_bulk(user, files)
    answers = dict.fromkeys(uuids, UNAVAILABLE)
    for file_obj in files:
        if permissions.get(file_obj.pk) is None:
            continue
        if file_obj.deleted_at is not None:
            answers[file_obj.uuid] = {"status": "trashed", "name": file_obj.name}
        elif is_blocked(file_obj):
            answers[file_obj.uuid] = {"status": "quarantined", "name": file_obj.name}
        else:
            answers[file_obj.uuid] = _available(file_obj)
    return answers

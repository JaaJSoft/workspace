"""Resolution and input sanitising for the public (unauthenticated) link paths.

Everything an anonymous visitor names goes through here before it reaches the
ORM or the storage layer.
"""

from __future__ import annotations

import posixpath
import re

from workspace.common.uuids import parse_uuid_or_none

from ..models import File

# Anything the filesystem or a log line would rather not carry.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_NAME_LENGTH = 255
FALLBACK_NAME = "upload"
# Longer than this and the tail is not an extension worth preserving.
_MAX_EXTENSION_LENGTH = 10


def resolve_within(link, node_uuid):
    """The live node *node_uuid* names, if it is *link*'s root or below it.

    ``None`` for anything else, so every caller answers 404 and no response
    distinguishes "outside your subtree" from "does not exist".

    The owner and group are compared before the path prefix because ``path``
    is not globally unique: two users can both own ``Shared/report.pdf``, and
    a bare prefix test would hand a visitor the wrong one.
    """
    root = link.file
    parsed = parse_uuid_or_none(node_uuid)
    if parsed is None:
        return None

    node = File.objects.filter(uuid=parsed, deleted_at__isnull=True).first()
    if node is None:
        return None
    if node.owner_id != root.owner_id or node.group_id != root.group_id:
        return None
    if node.pk == root.pk:
        return node

    root_path = root.path or root.get_path()
    node_path = node.path or node.get_path()
    if not root_path or not node_path.startswith(f"{root_path}/"):
        return None
    return node


def sanitize_upload_name(raw):
    """A safe ``File.name`` for an anonymously uploaded blob.

    Never empty, never a path, never longer than the column. Django's storage
    layer would also refuse a traversal, but as a SuspiciousFileOperation 500
    rather than a clean rejection, and the row's own name must not hold a
    separator either.
    """
    name = _CONTROL_CHARS.sub("", str(raw or "")).replace("\\", "/")
    name = posixpath.basename(name).strip().strip(".").strip()
    if not name:
        return FALLBACK_NAME
    if len(name) > MAX_NAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= _MAX_EXTENSION_LENGTH:
            name = f"{stem[: MAX_NAME_LENGTH - len(extension) - 1]}.{extension}"
        else:
            name = name[:MAX_NAME_LENGTH]
    return name or FALLBACK_NAME


def schedule_upload_notification(link):
    """Coalesce an owner notification for *link*. Filled in by the task layer."""
    return None

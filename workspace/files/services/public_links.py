"""Resolution and input sanitising for the public (unauthenticated) link paths.

Everything an anonymous visitor names goes through here before it reaches the
ORM or the storage layer.
"""

from __future__ import annotations

import logging
import posixpath
import re

from django.db.models import Q

from workspace.common.uuids import parse_uuid_or_none

from ..models import File

logger = logging.getLogger(__name__)

# Anything the filesystem or a log line would rather not carry.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_NAME_LENGTH = 255
FALLBACK_NAME = "upload"
# Longer than this and the tail is not an extension worth preserving.
_MAX_EXTENSION_LENGTH = 10
# The longest suffix unique_copy_name can append on a name clash. Reserved so
# a maximum-length upload still fits File.name after being renamed.
_RENAME_SUFFIX_HEADROOM = len(" (Copy 9999)")
MAX_UPLOAD_NAME_LENGTH = MAX_NAME_LENGTH - _RENAME_SUFFIX_HEADROOM


def scope_q(root):
    """Rows sharing *root*'s name namespace, as ``_names.sibling_nodes`` defines it.

    A group folder's children each carry their creator as ``owner``, so scoping
    a group root by owner would hide every file a second member added. Outside a
    group, the namespace is one user's personal tree.

    Both the resolver and the listing filter on this, so what a visitor can see
    and what they can open cannot drift apart.
    """
    if root.group_id:
        return Q(group_id=root.group_id)
    return Q(owner_id=root.owner_id, group__isnull=True)


def resolve_within(link, node_uuid):
    """The live node *node_uuid* names, if it is *link*'s root or below it.

    ``None`` for anything else, so every caller answers 404 and no response
    distinguishes "outside your subtree" from "does not exist".

    The namespace is narrowed before the path prefix because ``path`` is not
    globally unique: two users can both own ``Shared/report.pdf``, and a bare
    prefix test would hand a visitor the wrong one.
    """
    root = link.file
    parsed = parse_uuid_or_none(node_uuid)
    if parsed is None:
        return None

    node = File.objects.filter(
        scope_q(root), uuid=parsed, deleted_at__isnull=True
    ).first()
    if node is None:
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

    Never empty, never a path, never long enough to overflow the column once
    a name clash renames it. Django's storage layer would also refuse a
    traversal, but as a SuspiciousFileOperation 500 rather than a clean
    rejection, and the row's own name must not hold a separator either.
    """
    name = _CONTROL_CHARS.sub("", str(raw or "")).replace("\\", "/")
    name = posixpath.basename(name).strip().strip(".").strip()
    if not name:
        return FALLBACK_NAME
    if len(name) > MAX_UPLOAD_NAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= _MAX_EXTENSION_LENGTH:
            name = f"{stem[: MAX_UPLOAD_NAME_LENGTH - len(extension) - 1]}.{extension}"
        else:
            name = name[:MAX_UPLOAD_NAME_LENGTH]
    return name or FALLBACK_NAME


def upload_notification_cache_key(link_uuid):
    return f"files:drop-notify:{link_uuid}"


def schedule_upload_notification(link):
    """Arrange for one notification per burst of uploads on *link*.

    ``cache.add`` is the election: the first upload of a burst wins it and
    schedules the task, later ones find the key present and do nothing. The
    task deletes the key as its last action, so the window is exactly "first
    upload until the notification is sent". Letting the key expire on its own
    would both double-schedule (an upload between expiry and execution) and
    swallow uploads (one arriving after execution while the key still lived).
    """
    from django.conf import settings
    from django.core.cache import cache

    from ..tasks import notify_share_link_uploads

    window = settings.FILES_DROP_NOTIFY_WINDOW_SECONDS
    if not cache.add(upload_notification_cache_key(link.uuid), 1, timeout=window * 5):
        return None
    try:
        notify_share_link_uploads.apply_async(args=[str(link.uuid)], countdown=window)
    except Exception:
        # The bytes are already stored and the row is committed. A broker that
        # is down costs the owner a notification; it must not turn an accepted
        # upload into a 500.
        cache.delete(upload_notification_cache_key(link.uuid))
        logger.exception("Could not schedule the upload notification")
    return None

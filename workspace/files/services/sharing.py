"""Service helpers for file/folder sharing operations.

These wrap the FileShare / FileShareLink ORM mutations so callers
(viewsets, REST endpoints, tasks) get consistent event recording for
free. Notification dispatch stays in the calling layer because the
'who to notify' decision depends on context (a sync-time mutation
might want to skip notifications, a user request always sends them).
"""

from django.contrib.auth.hashers import make_password
from rest_framework.exceptions import APIException

from ..models import File, FileEvent, FileShare, FileShareLink
from .events import record_event

# Ceilings of the columns the caps land in. Above them PostgreSQL raises a
# DataError and SQLite silently stores an oversized row, so refuse the value
# here rather than at the database.
MAX_FILE_BYTES_CEILING = 9223372036854775807  # PositiveBigIntegerField
MAX_FILE_COUNT_CEILING = 2147483647  # PositiveIntegerField


class ShareLinkRuleError(APIException):
    """Share link parameters refused before anything was created.

    A DRF exception so the endpoint answers 400 through the default handler.
    The view must not catch a broad ValueError and echo its text: that is how
    an internal message reaches a response.
    """

    status_code = 400
    default_code = "invalid_share_link"
    default_detail = "Invalid share link parameters."


def share_file(file_obj, *, target_user, permission, acting_user):
    """Share a file with a user, or update the permission if already shared.

    Returns ``(share, created, permission_changed)``:
      - ``share`` is the FileShare row (created or updated).
      - ``created`` is True only when a brand-new share was inserted.
      - ``permission_changed`` is True when an existing share's permission
        was updated; False otherwise (including when ``created`` is True).
    """
    share, created = FileShare.objects.get_or_create(
        file=file_obj,
        shared_with=target_user,
        defaults={"shared_by": acting_user, "permission": permission},
    )
    if created:
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.SHARED,
            {
                "shared_with_id": target_user.pk,
                "shared_with_username": target_user.username,
                "permission": permission,
            },
        )
        return share, True, False

    if share.permission != permission:
        old_permission = share.permission
        share.permission = permission
        share.save(update_fields=["permission"])
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.SHARE_PERMISSION_CHANGED,
            {
                "shared_with_id": target_user.pk,
                "shared_with_username": target_user.username,
                "old_permission": old_permission,
                "new_permission": permission,
            },
        )
        return share, False, True

    return share, False, False


def unshare_file(file_obj, *, target_user, acting_user):
    """Remove a share. Returns the number of rows deleted (0 or 1)."""
    deleted, _ = FileShare.objects.filter(
        file=file_obj,
        shared_with=target_user,
    ).delete()
    if deleted:
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.UNSHARED,
            {
                "shared_with_id": target_user.pk,
                "shared_with_username": target_user.username,
            },
        )
    return deleted


def create_share_link(
    file_obj,
    *,
    acting_user,
    password="",
    expires_at=None,
    mode=FileShareLink.Mode.READ,
    max_file_bytes=None,
    max_file_count=None,
):
    """Create a public share link, optionally password-protected and time-limited.

    A file target is read-only by construction: there is nowhere to upload to.
    Only a folder can carry a mode that accepts anonymous writes.
    """
    mode = mode or FileShareLink.Mode.READ
    if mode not in FileShareLink.Mode.values:
        raise ShareLinkRuleError("Unknown share link mode.")
    if mode != FileShareLink.Mode.READ and file_obj.node_type != File.NodeType.FOLDER:
        raise ShareLinkRuleError(
            "Only a folder can accept uploads through a share link."
        )
    for cap, ceiling in (
        (max_file_bytes, MAX_FILE_BYTES_CEILING),
        (max_file_count, MAX_FILE_COUNT_CEILING),
    ):
        if cap is None:
            continue
        if cap < 1:
            raise ShareLinkRuleError("A cap must be a positive number.")
        if cap > ceiling:
            raise ShareLinkRuleError("A cap is larger than this server can store.")

    link = FileShareLink.objects.create(
        file=file_obj,
        created_by=acting_user,
        password=make_password(password) if password else "",
        expires_at=expires_at,
        mode=mode,
        max_file_bytes=max_file_bytes,
        max_file_count=max_file_count,
    )
    record_event(
        file_obj,
        acting_user,
        FileEvent.Action.LINK_CREATED,
        {
            "link_uuid": str(link.uuid),
            "mode": link.mode,
            "has_password": link.has_password,
            "has_expiry": link.expires_at is not None,
        },
    )
    return link


def revoke_share_link(file_obj, *, link_uuid, acting_user):
    """Revoke a public share link by uuid. Returns the number of rows deleted (0 or 1)."""
    deleted, _ = FileShareLink.objects.filter(
        uuid=link_uuid,
        file=file_obj,
    ).delete()
    if deleted:
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.LINK_REVOKED,
            {
                "link_uuid": str(link_uuid),
            },
        )
    return deleted

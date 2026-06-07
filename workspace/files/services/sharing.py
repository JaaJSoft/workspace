"""Service helpers for file/folder sharing operations.

These wrap the FileShare / FileShareLink ORM mutations so callers
(viewsets, REST endpoints, tasks) get consistent event recording for
free. Notification dispatch stays in the calling layer because the
'who to notify' decision depends on context (a sync-time mutation
might want to skip notifications, a user request always sends them).
"""

from django.contrib.auth.hashers import make_password

from ..models import FileEvent, FileShare, FileShareLink
from .events import record_event


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


def create_share_link(file_obj, *, acting_user, password="", expires_at=None):
    """Create a public share link, optionally password-protected and time-limited."""
    link = FileShareLink.objects.create(
        file=file_obj,
        created_by=acting_user,
        password=make_password(password) if password else "",
        expires_at=expires_at,
    )
    record_event(
        file_obj,
        acting_user,
        FileEvent.Action.LINK_CREATED,
        {
            "link_uuid": str(link.uuid),
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

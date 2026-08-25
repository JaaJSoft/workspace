"""Storage quota resolution and enforcement.

Two buckets, never both: a file counts against its group's quota when
``File.group`` is set, against its owner's personal quota otherwise. Trashed
rows keep counting - they still occupy disk, which is what makes emptying the
trash a real remedy and what lets a restore skip any check.

Every read of the quota tables and every write decision goes through this
module; nothing else may compute "bytes used".
"""

from django.conf import settings

from ..models import GroupStorageQuota, UserStorageQuota


def _pk(value):
    """Accept either a model instance or a primary key."""
    return getattr(value, "pk", value)


def effective_quota(user):
    """Bytes *user* may hold in personal files. ``None`` means unlimited.

    No row at all means the deployment-wide default; a row with an empty
    ``quota_bytes`` is an explicit exemption.
    """
    row = UserStorageQuota.objects.filter(user_id=_pk(user)).first()
    if row is None:
        return settings.STORAGE_QUOTA_BYTES
    return row.quota_bytes


def effective_group_quota(group):
    """Bytes *group* may hold in its folder. ``None`` means unlimited."""
    if group is None:
        return None
    row = GroupStorageQuota.objects.filter(group_id=_pk(group)).first()
    return row.quota_bytes if row is not None else None

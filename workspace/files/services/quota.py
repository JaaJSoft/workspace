"""Storage quota resolution and enforcement.

Two buckets, never both: a file counts against its group's quota when
``File.group`` is set, against its owner's personal quota otherwise. Trashed
rows count.

Nothing outside this module computes bucket usage or reads the quota tables.
"""

from django.conf import settings

from ..models import GroupStorageQuota, UserStorageQuota


def _pk(value):
    """Accept either a model instance or a primary key."""
    return getattr(value, "pk", value)


def effective_quota(user):
    """Bytes *user* may hold in personal files. ``None`` means unlimited."""
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

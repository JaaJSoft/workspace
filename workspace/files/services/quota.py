"""Storage quota resolution and enforcement.

Two buckets, never both: a file counts against its group's quota when
``File.group`` is set, against its owner's personal quota otherwise. Trashed
rows count.

Nothing outside this module computes bucket usage or reads the quota tables.
"""

from django.conf import settings
from django.db.models import Sum
from django.template.defaultfilters import filesizeformat
from rest_framework.exceptions import APIException

from ..models import File, GroupStorageQuota, UserStorageQuota


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


def personal_usage(user):
    """Bytes held by *user*'s personal files, trashed rows included."""
    total = File.objects.filter(
        owner_id=_pk(user),
        group__isnull=True,
        node_type=File.NodeType.FILE,
    ).aggregate(total=Sum("size"))["total"]
    return total or 0


def group_usage(group):
    """Bytes held in *group*'s folder, trashed rows included."""
    total = File.objects.filter(
        group_id=_pk(group),
        node_type=File.NodeType.FILE,
    ).aggregate(total=Sum("size"))["total"]
    return total or 0


def _group_name(group):
    name = getattr(group, "name", None)
    if name is not None:
        return name
    from django.contrib.auth.models import Group

    return Group.objects.filter(pk=group).values_list("name", flat=True).first() or "?"


def bucket_state(*, owner, group):
    """Return ``(used, limit, label)`` for the bucket a write would land in.

    ``limit`` is ``None`` when unlimited; ``used`` is then left at 0 rather
    than aggregated.
    """
    if group is not None:
        limit = effective_group_quota(group)
        used = 0 if limit is None else group_usage(group)
        return used, limit, f'The group folder "{_group_name(group)}"'
    limit = effective_quota(owner)
    used = 0 if limit is None else personal_usage(owner)
    return used, limit, "Your personal storage"


def remaining_bytes(*, owner, group):
    """Bytes still writable in the bucket, or ``None`` when unlimited.

    Negative when a quota was lowered below current usage.
    """
    used, limit, _ = bucket_state(owner=owner, group=group)
    return None if limit is None else limit - used


class QuotaExceeded(APIException):
    """A write refused because its bucket is full.

    A DRF exception so REST endpoints answer 413 through the default handler.
    """

    status_code = 413
    default_code = "storage_quota_exceeded"
    default_detail = "Storage quota exceeded."


def check_write_allowed(*, owner, group, additional_bytes):
    """Raise ``QuotaExceeded`` when *additional_bytes* would not fit.

    The bucket comes from the target row, not from whoever is acting: writing
    into a teammate's file charges that file's bucket. A delta of zero or less
    is always allowed, so a shrinking file stays saveable over quota.
    """
    if not additional_bytes or additional_bytes <= 0:
        return
    used, limit, label = bucket_state(owner=owner, group=group)
    if limit is None or used + additional_bytes <= limit:
        return
    remedy = (
        "Free up space or empty the trash."
        if group is None
        else "Free up space or ask an administrator to raise the limit."
    )
    raise QuotaExceeded(
        f"{label} is full ({filesizeformat(used)} of {filesizeformat(limit)} used) "
        f"and this write needs {filesizeformat(additional_bytes)}. {remedy}"
    )

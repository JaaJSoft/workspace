"""Storage quota resolution and enforcement.

Two buckets, never both: a file counts against its group's quota when
``File.group`` is set, against its owner's personal quota otherwise. Trashed
rows count.

Bucket usage is computed here and nowhere else; the admin is the only other
reader of the quota tables.
"""

from django.conf import settings
from django.db.models import OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
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


def personal_usage_subquery(user_field="user_id"):
    """``personal_usage`` as an annotation over a queryset of quota rows.

    Same bucket definition as the aggregate above, expressed so a listing
    resolves every row in one query instead of one aggregate per row.
    """
    return Coalesce(
        Subquery(
            File.objects.filter(
                owner_id=OuterRef(user_field),
                group__isnull=True,
                node_type=File.NodeType.FILE,
            )
            .values("owner_id")
            .annotate(total=Sum("size"))
            .values("total")
        ),
        Value(0),
    )


def group_usage_subquery(group_field="group_id"):
    """``group_usage`` as an annotation over a queryset of quota rows."""
    return Coalesce(
        Subquery(
            File.objects.filter(
                group_id=OuterRef(group_field),
                node_type=File.NodeType.FILE,
            )
            .values("group_id")
            .annotate(total=Sum("size"))
            .values("total")
        ),
        Value(0),
    )


def _bucket_label(group):
    """Name the bucket in a refusal message.

    Costs a query when *group* is a bare primary key, which is why it is only
    reached once a write has already been refused.
    """
    if group is None:
        return "Your personal storage"
    name = getattr(group, "name", None)
    if name is None:
        from django.contrib.auth.models import Group

        name = (
            Group.objects.filter(pk=group).values_list("name", flat=True).first() or "?"
        )
    return f'The group folder "{name}"'


def bucket_state(*, owner, group):
    """Return ``(used, limit)`` for the bucket a write would land in.

    ``limit`` is ``None`` when unlimited; ``used`` is then left at 0 rather
    than aggregated.
    """
    if group is not None:
        limit = effective_group_quota(group)
        return (0 if limit is None else group_usage(group)), limit
    limit = effective_quota(owner)
    return (0 if limit is None else personal_usage(owner)), limit


def remaining_bytes(*, owner, group):
    """Bytes still writable in the bucket, or ``None`` when unlimited.

    Negative when a quota was lowered below current usage.
    """
    used, limit = bucket_state(owner=owner, group=group)
    return None if limit is None else limit - used


def usage_percent(used, limit, *, ndigits=None):
    """Share of *limit* consumed by *used*, capped at 100.

    ``None`` means unlimited. A limit of zero is a valid, fully-consumed
    bucket (an administrator freezing an account) - it is never treated as
    unlimited, and always reports 100.
    """
    if limit is None:
        return None
    if limit == 0:
        return 100
    share = 100 * used / limit
    return min(100, round(share) if ndigits is None else round(share, ndigits))


class QuotaExceeded(APIException):
    """A write refused because its bucket is full.

    A DRF exception so REST endpoints answer 413 through the default handler.
    """

    status_code = 413
    default_code = "storage_quota_exceeded"
    default_detail = "Storage quota exceeded."


def subtree_bytes(node, *, include_trashed):
    """Bytes stored by *node* and its descendants.

    ``include_trashed`` follows what the caller is about to do: a move carries
    trashed descendants along, a copy only duplicates the live ones.
    """
    qs = File.objects.filter(node._descendant_filter(), node_type=File.NodeType.FILE)
    if not include_trashed:
        qs = qs.filter(deleted_at__isnull=True)
    return qs.aggregate(total=Sum("size"))["total"] or 0


def check_write_allowed(*, owner, group, additional_bytes):
    """Raise ``QuotaExceeded`` when *additional_bytes* would not fit.

    The bucket comes from the target row, not from whoever is acting: writing
    into a teammate's file charges that file's bucket. A delta of zero or less
    is always allowed, so a shrinking file stays saveable over quota.
    """
    if not additional_bytes or additional_bytes <= 0:
        return
    used, limit = bucket_state(owner=owner, group=group)
    if limit is None or used + additional_bytes <= limit:
        return
    label = _bucket_label(group)
    remedy = (
        "Free up space or empty the trash."
        if group is None
        else "Free up space or ask an administrator to raise the limit."
    )
    raise QuotaExceeded(
        f"{label} is full ({filesizeformat(used)} of {filesizeformat(limit)} used) "
        f"and this write needs {filesizeformat(additional_bytes)}. {remedy}"
    )

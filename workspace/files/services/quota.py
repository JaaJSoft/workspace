"""Storage quota resolution and enforcement.

Two buckets, never both: a file counts against its group's quota when
``File.group`` is set, against its owner's personal quota otherwise. Trashed
rows count.

Bucket usage is computed here and nowhere else; the admin is the only other
reader of the quota tables.

A write is checked against what the bucket holds plus what open upload
sessions have reserved in it. Usage as shown to people leaves reservations
out: they are bytes still on their way.
"""

from django.conf import settings
from django.db.models import BigIntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext, gettext_lazy
from rest_framework.exceptions import APIException

from ..models import File, GroupStorageQuota, UploadSession, UserStorageQuota


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


def _group_name(group):
    """Name the group bucket in a refusal message.

    Costs a query when *group* is a bare primary key, which is why it is only
    reached once a write has already been refused.
    """
    name = getattr(group, "name", None)
    if name is None:
        from django.contrib.auth.models import Group

        name = (
            Group.objects.filter(pk=group).values_list("name", flat=True).first() or "?"
        )
    return name


def _open_sessions(*, owner, group, excluding=None):
    sessions = UploadSession.objects.filter(completed_at__isnull=True)
    if group is not None:
        sessions = sessions.filter(group_id=_pk(group))
    else:
        sessions = sessions.filter(owner_id=_pk(owner), group__isnull=True)
    if excluding is not None:
        sessions = sessions.exclude(pk=_pk(excluding))
    return sessions


def reserved_bytes(*, owner, group, excluding=None):
    """Bytes the open upload sessions of a bucket have declared.

    *excluding* leaves one session out: the one being finalized, whose
    reservation is the very write being checked.
    """
    sessions = _open_sessions(owner=owner, group=group, excluding=excluding)
    return sessions.aggregate(total=Sum("size"))["total"] or 0


def bucket_state(*, owner, group, excluding=None):
    """Return ``(used, limit)`` for the bucket a write would land in.

    ``used`` counts the open upload sessions' reservations, *excluding* one,
    in the same query as the files. ``limit`` is ``None`` when unlimited;
    ``used`` is then left at 0 rather than aggregated.
    """
    if group is not None:
        limit = effective_group_quota(group)
        files = File.objects.filter(group_id=_pk(group))
        bucket_key = "group_id"
    else:
        limit = effective_quota(owner)
        files = File.objects.filter(owner_id=_pk(owner), group__isnull=True)
        bucket_key = "owner_id"
    if limit is None:
        return 0, None
    sessions = _open_sessions(owner=owner, group=group, excluding=excluding)
    reserved = Subquery(
        sessions.values(bucket_key).annotate(total=Sum("size")).values("total"),
        output_field=BigIntegerField(),
    )
    used = files.filter(node_type=File.NodeType.FILE).aggregate(
        total=Coalesce(Sum("size"), Value(0)) + Coalesce(reserved, Value(0))
    )["total"]
    return used, limit


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
    default_detail = gettext_lazy("Storage quota exceeded.")


def subtree_bytes(node, *, include_trashed):
    """Bytes stored by *node* and its descendants.

    ``include_trashed`` follows what the caller is about to do: a move carries
    trashed descendants along, a copy only duplicates the live ones.
    """
    qs = File.objects.filter(node._descendant_filter(), node_type=File.NodeType.FILE)
    if not include_trashed:
        qs = qs.filter(deleted_at__isnull=True)
    return qs.aggregate(total=Sum("size"))["total"] or 0


def check_write_allowed(*, owner, group, additional_bytes, excluding=None):
    """Raise ``QuotaExceeded`` when *additional_bytes* would not fit.

    The bucket comes from the target row, not from whoever is acting: writing
    into a teammate's file charges that file's bucket. A delta of zero or less
    is always allowed, so a shrinking file stays saveable over quota.

    *excluding* is the upload session the bytes come from, whose reservation
    they are, and which must not be counted twice.
    """
    if not additional_bytes or additional_bytes <= 0:
        return
    used, limit = bucket_state(owner=owner, group=group, excluding=excluding)
    if limit is None or used + additional_bytes <= limit:
        return
    sizes = {
        "used": filesizeformat(used),
        "limit": filesizeformat(limit),
        "needed": filesizeformat(additional_bytes),
    }
    if group is None:
        message = (
            gettext(
                "Your personal storage is full (%(used)s of %(limit)s used) and "
                "this write needs %(needed)s. Free up space or empty the trash."
            )
            % sizes
        )
    else:
        message = gettext(
            'The group folder "%(name)s" is full (%(used)s of %(limit)s used) and '
            "this write needs %(needed)s. Free up space or ask an administrator "
            "to raise the limit."
        ) % {**sizes, "name": _group_name(group)}
    raise QuotaExceeded(message)

"""Storage analysis: what takes up space under a folder (or an account root).

Every figure comes out of an aggregate query over the subtree - categories,
per-subfolder totals, largest files and duplicate groups - so the cost grows
with the number of *folders* and result rows, never with the number of files.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Count, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Concat

from ..models import File

LARGEST_FILES_LIMIT = 20
DUPLICATE_GROUPS_LIMIT = 20
# Slices kept in the category chart; everything past that folds into "Other".
CATEGORY_SLICES = 6

# Display metadata per ``File.category`` value. The chart colours are daisyUI
# text tokens so the SVG follows the theme; they diverge from the icon colours
# of ``filetype`` on purpose - there, four groups share the same muted grey,
# which would make adjacent slices indistinguishable.
CATEGORY_META = {
    "image": {"label": "Images", "icon": "image", "css_class": "text-success"},
    "video": {"label": "Videos", "icon": "video", "css_class": "text-error"},
    "audio": {"label": "Audio", "icon": "music", "css_class": "text-secondary"},
    "document": {
        "label": "Documents",
        "icon": "file-text",
        "css_class": "text-primary",
    },
    "archive": {
        "label": "Archives",
        "icon": "file-archive",
        "css_class": "text-warning",
    },
    "code": {"label": "Code", "icon": "file-code", "css_class": "text-info"},
    "text": {"label": "Text", "icon": "file-type", "css_class": "text-accent"},
    "font": {"label": "Fonts", "icon": "type", "css_class": "text-neutral"},
    "executable": {
        "label": "Executables",
        "icon": "binary",
        "css_class": "text-neutral",
    },
    "application": {
        "label": "Applications",
        "icon": "file",
        "css_class": "text-neutral",
    },
}
OTHER_META = {
    "label": "Other",
    "icon": "file-question",
    "css_class": "text-base-content/40",
}


@dataclass(frozen=True)
class StorageScope:
    """The subtree being analysed.

    ``folder`` is ``None`` for the user's personal root. A group root folder
    (``group`` set, ``parent`` null) is treated as a root too: it gets the
    trash figures of the group.
    """

    user: object
    folder: File | None

    @property
    def group(self):
        return self.folder.group if self.folder is not None else None

    @property
    def is_root(self):
        return self.folder is None or (
            self.folder.group_id is not None and self.folder.parent_id is None
        )

    def tree_q(self):
        """Filter matching every live node strictly below the scope."""
        if self.folder is None:
            return Q(owner=self.user, group__isnull=True, deleted_at__isnull=True)
        path = self.folder.path or self.folder.get_path()
        q = Q(path__startswith=f"{path}/", deleted_at__isnull=True)
        if self.folder.group_id:
            return q & Q(group=self.folder.group)
        return q & Q(owner=self.folder.owner, group__isnull=True)

    def trash_q(self):
        if self.group is not None:
            return Q(group=self.group, deleted_at__isnull=False)
        return Q(owner=self.user, deleted_at__isnull=False)


def _files(scope):
    return File.objects.filter(scope.tree_q(), node_type=File.NodeType.FILE)


def _totals(qs):
    return qs.aggregate(size=Coalesce(Sum("size"), 0), count=Count("pk"))


def category_breakdown(scope):
    rows = list(
        _files(scope)
        .values("category")
        .annotate(size=Coalesce(Sum("size"), 0), count=Count("pk"))
        .order_by("-size")
    )
    entries = []
    other = {"key": "other", "size": 0, "count": 0, **OTHER_META}
    for row in rows:
        meta = CATEGORY_META.get(row["category"])
        if meta is None or len(entries) >= CATEGORY_SLICES:
            other["size"] += row["size"]
            other["count"] += row["count"]
            continue
        entries.append(
            {"key": row["category"], "size": row["size"], "count": row["count"], **meta}
        )
    if other["count"]:
        entries.append(other)
    total = sum(e["size"] for e in entries)
    for entry in entries:
        entry["percent"] = round(100 * entry["size"] / total, 1) if total else 0.0
    return entries


def subfolder_breakdown(scope):
    """Direct child folders with their recursive size, largest first.

    Files sitting directly in the folder are reported as one extra entry
    (``uuid`` None) so the rows add up to the folder total; ``percent`` is
    each row's share of that total.
    """
    if scope.folder is None:
        children = File.objects.filter(
            owner=scope.user, group__isnull=True, parent__isnull=True
        )
    else:
        children = File.objects.filter(parent=scope.folder)
    children = children.filter(deleted_at__isnull=True)

    # Correlated aggregate over the child's subtree. The constant ``one``
    # annotation is what the subquery groups by, so it collapses to a
    # single row per outer folder.
    descendants = File.objects.filter(
        path__startswith=Concat(OuterRef("path"), Value("/")),
        node_type=File.NodeType.FILE,
        deleted_at__isnull=True,
    )
    if scope.group is not None:
        descendants = descendants.filter(group=scope.group)
    else:
        descendants = descendants.filter(owner=scope.user, group__isnull=True)
    descendants = descendants.order_by().annotate(one=Value(1)).values("one")

    folders = (
        children.filter(node_type=File.NodeType.FOLDER)
        .annotate(
            tree_size=Coalesce(
                Subquery(descendants.annotate(s=Sum("size")).values("s")[:1]), 0
            ),
            tree_count=Coalesce(
                Subquery(descendants.annotate(c=Count("pk")).values("c")[:1]), 0
            ),
        )
        .order_by("-tree_size", "name")
    )
    entries = [
        {
            "uuid": str(f.uuid),
            "name": f.name,
            "icon": f.icon or "folder",
            "color": f.color or "text-warning",
            "size": f.tree_size,
            "count": f.tree_count,
        }
        for f in folders
    ]
    loose = _totals(children.filter(node_type=File.NodeType.FILE))
    if loose["count"]:
        entries.append(
            {
                "uuid": None,
                "name": "Files in this folder",
                "icon": "file",
                "color": "text-base-content/60",
                "size": loose["size"],
                "count": loose["count"],
            }
        )
    entries.sort(key=lambda e: e["size"], reverse=True)
    total = sum(e["size"] for e in entries)
    for entry in entries:
        entry["percent"] = round(100 * entry["size"] / total, 1) if total else 0.0
    return entries


def _file_entry(f):
    return {
        "uuid": str(f.uuid),
        "name": f.name,
        "path": f.path,
        "size": f.size or 0,
        "type": f.type,
        "category": f.category,
        "parent": str(f.parent_id) if f.parent_id else None,
    }


def largest_files(scope, *, category=None, limit=LARGEST_FILES_LIMIT):
    qs = _files(scope)
    if category:
        qs = qs.filter(category=category)
    qs = qs.order_by("-size", "name").only(
        "uuid", "name", "path", "size", "type", "category", "parent"
    )[:limit]
    return [_file_entry(f) for f in qs]


def duplicate_groups(scope, *, limit=DUPLICATE_GROUPS_LIMIT):
    """Same-content files (2+ live copies), by wasted bytes descending."""
    groups = list(
        _files(scope)
        .exclude(content_hash="")
        .values("content_hash")
        .annotate(
            copies=Count("pk"),
            biggest=Coalesce(Max("size"), 0),
            wasted=Coalesce(Sum("size"), 0) - Coalesce(Max("size"), 0),
        )
        .filter(copies__gt=1)
        .order_by("-wasted", "content_hash")[:limit]
    )
    if not groups:
        return []
    by_hash = {g["content_hash"]: [] for g in groups}
    copies = (
        _files(scope)
        .filter(content_hash__in=list(by_hash))
        .order_by("path")
        .only(
            "uuid", "name", "path", "size", "type", "category", "parent", "content_hash"
        )
    )
    for f in copies:
        by_hash[f.content_hash].append(_file_entry(f))
    return [
        {
            "content_hash": g["content_hash"],
            "size": g["biggest"],
            "copies": g["copies"],
            "wasted": g["wasted"],
            "files": by_hash[g["content_hash"]],
        }
        for g in groups
    ]


def trash_summary(scope):
    return _totals(File.objects.filter(scope.trash_q(), node_type=File.NodeType.FILE))


def analyze_storage(user, folder=None, *, category=None):
    """Return the storage breakdown of *folder* (or the user's root).

    The caller is responsible for the access check on *folder*; the scope
    only decides which rows belong to the subtree.
    """
    scope = StorageScope(user=user, folder=folder)
    totals = _totals(_files(scope))
    folder_count = (
        File.objects.filter(scope.tree_q(), node_type=File.NodeType.FOLDER)
        .order_by()
        .count()
    )
    result = {
        "folder": (
            {
                "uuid": str(folder.uuid),
                "name": folder.name,
                "path": folder.path,
                "parent": str(folder.parent_id) if folder.parent_id else None,
                "group": folder.group.name if folder.group_id else None,
            }
            if folder is not None
            else None
        ),
        "is_root": scope.is_root,
        "total_size": totals["size"],
        "file_count": totals["count"],
        "folder_count": folder_count,
        "categories": category_breakdown(scope),
        "subfolders": subfolder_breakdown(scope),
        "largest_files": largest_files(scope, category=category),
        "largest_files_category": category or None,
        "duplicates": duplicate_groups(scope),
        "trash": trash_summary(scope) if scope.is_root else None,
        "quota": settings.STORAGE_QUOTA_BYTES if folder is None else None,
    }
    return result

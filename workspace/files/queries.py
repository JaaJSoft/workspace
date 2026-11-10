"""Which files a user can reach, and what they may do with each.

The querysets and filters every listing starts from, and the permission a
user holds on a node. One definition of the access branches feeds them all,
so a view, a WebDAV handler, an AI tool and a task cannot disagree about who
sees what.
"""

import enum

from django.contrib.auth.models import Group
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q

from .models import File, FileFavorite, FileShare, PinnedFolder


class FilePermission(enum.IntEnum):
    """Permission levels for file access, ordered by capability."""

    VIEW = 10  # share-ro: read/download only
    WRITE = 20  # share-rw: update content only
    EDIT = 30  # group member: full CRUD
    MANAGE = 40  # owner: full control + share management


def _access_branches(user):
    """Return the per-branch filter kwargs for *user*'s accessible files.

    Single source of truth for the access branches (owned, group folder,
    shared with the user, shared with one of their groups, shared with a
    project they can open), consumed by
    both ``accessible_files_q`` (ORed into one Q across a join) and
    ``accessible_file_ids`` (each branch a separately indexed UNION arm). Defining the branches once keeps the two
    permission paths from drifting - a divergence would mean a leak or a
    hole in one of them.
    """
    from workspace.projects.queries import user_project_ids

    # Owning a group node grants nothing once its owner has left the
    # group: the group branch is what covers the group nodes they own.
    return (
        {"owner": user, "group__isnull": True},
        {"group__in": user.groups.all()},
        {"shares__shared_with": user},
        {"shares__shared_with_group__in": user.groups.all()},
        {"shares__shared_with_project__in": user_project_ids(user)},
    )


def access_arms(user):
    """``_access_branches`` with the group-folder branch split per group.

    ``group_id IN (...)`` cannot be read in index order, so a caller that
    wants the newest rows of each arm through an index range needs one
    equality per group.
    """
    arms = []
    for branch in _access_branches(user):
        if branch.keys() == {"group__in"}:
            group_ids = branch["group__in"].values_list("pk", flat=True)
            arms.extend({"group_id": group_id} for group_id in group_ids)
        else:
            arms.append(branch)
    return arms


def accessible_files_q(user):
    """Return a Q filter matching all files *user* can access."""
    # Seed from the first branch rather than an empty Q(): an empty Q in
    # an OR chain can match everything - a full access leak here.
    branches = _access_branches(user)
    q = Q(**branches[0])
    for branch in branches[1:]:
        q |= Q(**branch)
    return q


def accessible_file_ids(user, *, include_deleted=True):
    """Return a values queryset of ids of all files *user* can access.

    Same semantics as ``accessible_files_q`` (owned + group + shared,
    ``deleted_at`` not filtered by default), but built as a UNION of
    independently indexed queries, one per access branch. The Q form ORs
    across a join (``shares__shared_with``), which defeats per-branch
    index use and
    forces a full scan of the files table - O(total files) on every
    call. The UNION stays proportional to what the user can actually
    see, so hot paths (activity feed) should prefer this helper as a
    ``pk__in=`` / ``file_id__in=`` source.

    ``include_deleted=False`` excludes trashed files by filtering
    ``deleted_at`` inside each UNION arm, where it pairs with the
    composite indexes (owner/deleted_at, group/deleted_at). Callers must
    NOT add ``deleted_at__isnull=True`` to the outer query instead:
    ``deleted_at IS NULL`` matches almost every row, and the planner is
    prone to driving the whole query from that low-selectivity index -
    a full-table scan that this helper exists to avoid.

    UNION querysets are terminal: no further ``filter()``/``annotate()``
    is possible. When the access filter must compose with other
    conditions in the same query, keep using ``accessible_files_q``.
    The empty ``order_by()`` is required twice - ``File`` has a default
    ``Meta.ordering``. On each branch because ORDER BY is invalid inside
    a compound subquery, and on the combined queryset because a combined
    queryset falls back to ``Meta.ordering``, whose columns aren't in the
    single-column result set - DatabaseError at iteration.
    """
    live = {} if include_deleted else {"deleted_at__isnull": True}
    arms = [
        File.objects.filter(**branch, **live).order_by().values_list("pk", flat=True)
        for branch in _access_branches(user)
    ]
    return arms[0].union(*arms[1:]).order_by()


def user_files_qs(user):
    """Return a queryset of active (non-deleted) personal files owned by the user."""
    return File.objects.filter(owner=user, group__isnull=True, deleted_at__isnull=True)


def owned_files_q(user):
    """``owned_files_qs`` as a Q, for a query that already reads ``File``.

    *user* may be a user or a user id: the filter needs no row of theirs.
    """
    return Q(owner=user) & (
        Q(group__isnull=True) | Q(group__in=Group.objects.filter(user=user))
    )


def owned_files_qs(user):
    """The nodes *user* owns and still manages, ``deleted_at`` not filtered.

    Their personal nodes, plus the group nodes they created in groups they
    still belong to - not those of a group they have left, which stay with
    the group.
    """
    return File.objects.filter(owned_files_q(user))


def restorable_files_qs(user):
    """The nodes *user* may restore or purge once trashed, ``deleted_at``
    not filtered.

    Their personal nodes, plus every node of the groups they belong to,
    whoever trashed it: the queryset form of ``trash_permissions_bulk``.
    """
    return File.objects.filter(
        Q(owner=user, group__isnull=True) | Q(group__in=user.groups.all())
    )


def user_group_files_qs(user):
    """Return a queryset of active group files the user can access."""
    return File.objects.filter(
        group__in=user.groups.all(),
        deleted_at__isnull=True,
    )


def reads_full_path(permission):
    """Whether a viewer at *permission* may read a node's full path.

    At EDIT or above - their own tree, or a group they are in. Below that
    the node reached them through a share, and its path names the owner's
    private folders above it. ``full_path_q`` is the same rule in SQL.
    """
    return permission is not None and permission >= FilePermission.EDIT


def visible_path(file_obj, permission):
    """The path of *file_obj* a viewer at *permission* may read: the full
    path, or its name alone (see ``reads_full_path``)."""
    if reads_full_path(permission):
        return file_obj.path
    return file_obj.name


def visible_ancestors(user, folder, permission):
    """The live ancestor folders of *folder* that *user* may see, root first.

    *permission* is the viewer's on *folder*. One who reads its full path
    (``reads_full_path``: the owner, a group member) sees every folder
    above it. Anyone else reached it through a share, and only keeps the
    ancestors they can open themselves - never the owner's private folders
    above the shared one. One query for the whole chain, through the
    denormalized ``path``.
    """
    parts = (folder.path or folder.get_path()).split("/")
    ancestor_paths = ["/".join(parts[:i]) for i in range(1, len(parts))]
    if not ancestor_paths:
        return []
    # Scoped to the folder's tree: paths repeat across owners and groups.
    if folder.group_id:
        tree = Q(group_id=folder.group_id)
    else:
        tree = Q(owner_id=folder.owner_id, group__isnull=True)
    ancestors = File.objects.filter(
        tree,
        path__in=ancestor_paths,
        node_type=File.NodeType.FOLDER,
        deleted_at__isnull=True,
    ).only("uuid", "name", "icon", "color", "path")
    if not reads_full_path(permission):
        ancestors = ancestors.filter(accessible_files_q(user))
    by_path = {ancestor.path: ancestor for ancestor in ancestors}
    return [by_path[path] for path in ancestor_paths if path in by_path]


def full_path_q(user):
    """Nodes whose full path *user* may read, per ``visible_path``.

    Those they reach at EDIT or above: their personal nodes and every node
    of the groups they are in.
    """
    return Q(owner=user, group__isnull=True) | Q(group__in=user.groups.all())


def annotate_for_serializer(queryset, user):
    """Prepare a File queryset for ``FileSerializer``."""
    # Imported lazily: the services package imports this module.
    from .services.scanning.policy import with_scan
    from .services.tags import viewer_file_tags

    return with_scan(
        queryset.annotate(
            is_favorite=Exists(
                FileFavorite.objects.filter(owner=user, file_id=OuterRef("pk"))
            ),
            is_pinned=Exists(
                PinnedFolder.objects.filter(owner=user, folder_id=OuterRef("pk"))
            ),
            is_shared=Exists(FileShare.objects.filter(file_id=OuterRef("pk"))),
            has_children=Exists(
                File.objects.filter(
                    parent_id=OuterRef("pk"),
                    node_type=File.NodeType.FOLDER,
                    deleted_at__isnull=True,
                )
            ),
            full_path_visible=ExpressionWrapper(
                full_path_q(user), output_field=BooleanField()
            ),
        ).prefetch_related(viewer_file_tags(user))
    )


def get_permissions_bulk(user, file_objects):
    """Return ``{file.pk: FilePermission | None}`` for many files at once.

    Same semantics as :func:`get_permission`, but the group and share
    lookups are batched: at most two queries for the whole set instead
    of up to two per file. A file reached through both a user share and
    a group share gets the higher of the two permissions.
    """
    return _permissions_bulk(user, file_objects, in_trash=False)


def trash_permissions_bulk(user, file_objects):
    """``get_permissions_bulk`` as the trash sees it.

    The same answer for a live node. A trashed node grants nothing to
    anyone but its owner, except a group's: the trash is where the group
    recovers what one of its members deleted, so every current member gets
    EDIT on it - enough to preview, restore and purge it. A former member
    still gets nothing.
    """
    return _permissions_bulk(user, file_objects, in_trash=True)


def trash_permission(user, file_obj):
    """:func:`trash_permissions_bulk` for one node."""
    return trash_permissions_bulk(user, [file_obj])[file_obj.pk]


def _permissions_bulk(user, file_objects, *, in_trash):
    permissions = {}
    group_candidates = []
    share_candidates = []
    for file_obj in file_objects:
        if file_obj.group_id:
            # Ownership of a group node only counts while the owner is
            # still in the group, so it waits for the membership lookup.
            group_candidates.append(file_obj)
        elif file_obj.owner_id == user.id:
            permissions[file_obj.pk] = FilePermission.MANAGE
        elif file_obj.deleted_at is not None:
            permissions[file_obj.pk] = None
        else:
            share_candidates.append(file_obj)

    if group_candidates:
        group_ids = set(user.groups.values_list("id", flat=True))
        for file_obj in group_candidates:
            if file_obj.group_id in group_ids:
                if file_obj.owner_id == user.id:
                    permissions[file_obj.pk] = FilePermission.MANAGE
                elif file_obj.deleted_at is None or in_trash:
                    permissions[file_obj.pk] = FilePermission.EDIT
                else:
                    permissions[file_obj.pk] = None
            elif file_obj.deleted_at is not None:
                permissions[file_obj.pk] = None
            else:
                share_candidates.append(file_obj)

    if share_candidates:
        shares = {}
        for file_id, permission in (
            FileShare.objects.reaching(user)
            .filter(file_id__in=[f.pk for f in share_candidates])
            .values_list("file_id", "permission")
        ):
            granted = (
                FilePermission.WRITE if permission == "rw" else FilePermission.VIEW
            )
            shares[file_id] = max(shares.get(file_id, granted), granted)
        for file_obj in share_candidates:
            permissions[file_obj.pk] = shares.get(file_obj.pk)

    return permissions


def get_permission(user, file_obj):
    """Return the access permission level for *user* on *file_obj*."""
    return get_permissions_bulk(user, [file_obj])[file_obj.pk]


def can_access(user, file_obj):
    """Check whether *user* can access *file_obj*."""
    return get_permission(user, file_obj) is not None


def can_create_in(user, parent):
    """Whether *user* may create a file or folder directly under *parent*.

    Needs a live folder and EDIT on it: a read-write share only grants
    WRITE, which covers the shared node's content, not new rows under it.
    """
    return (
        parent.node_type == File.NodeType.FOLDER
        and parent.deleted_at is None
        and (get_permission(user, parent) or 0) >= FilePermission.EDIT
    )


def resolve_accessible_files(user, file_uuids):
    """Resolve *file_uuids* to live, accessible ``File`` rows, or None.

    Deduplicates the input, keeps only non-trashed regular files the
    user can access (permissions checked in one bulk pass), and returns
    None as soon as a single uuid is unknown, trashed, a folder, blocked
    by the malware policy or out of reach - callers attach
    all-or-nothing and must not leak which of the five it was.

    The malware exclusion belongs here rather than at each caller: every
    one of them copies the blob into a table with no scan row of its own
    (a chat attachment, an SMTP payload, a task attachment), so a blocked
    file that got through would never be re-examined.
    """
    # Imported lazily: the services package imports this module.
    from .services.scanning.policy import exclude_blocked

    file_uuids = list(dict.fromkeys(file_uuids))
    if not file_uuids:
        return []
    candidates = list(
        exclude_blocked(
            File.objects.filter(
                uuid__in=file_uuids,
                node_type=File.NodeType.FILE,
                deleted_at__isnull=True,
            )
        )
    )
    permissions = get_permissions_bulk(user, candidates)
    accessible = [f for f in candidates if permissions[f.pk] is not None]
    if len(accessible) != len(file_uuids):
        return None
    return accessible

"""Build a node/edge graph of files and their FileLink references.

Generic over file type: ``build_file_graph`` returns the files a user can see
(within a scope) as nodes, and the FileLink rows whose endpoints are BOTH in
that node set as edges. The notes "graph view" calls it with file_type="markdown";
journal coloring is computed by the notes frontend, not here.
"""

from __future__ import annotations

from ..models import File, FileLink
from . import FileService


def _folder_path(user, folder_uuid):
    """Return the denormalized path of a folder within the user's accessible files."""
    return (
        File.objects.filter(
            FileService.accessible_files_q(user),
            uuid=folder_uuid,
            deleted_at__isnull=True,
        )
        .values_list("path", flat=True)
        .first()
    )


def build_file_graph(
    user,
    *,
    scope="mine",
    file_type=None,
    under=None,
    exclude_descendants_of=None,
    favorites=None,
    search=None,
) -> dict:
    """Return ``{"nodes": [...], "edges": [...]}`` for *user* in *scope*.

    scope="mine": the user's own personal (non-deleted) files.
    scope="all":  every non-deleted file the user can access (owned/group/shared).
    Unknown scopes fall back to "mine" (the view validates and 400s first).

    Generic filters, all intersected with the scope set:
    - ``under`` (folder UUID): keep only that folder's subtree (path prefix). An
      ``under`` folder the user cannot see yields an empty graph.
    - ``exclude_descendants_of`` (folder UUID): drop that folder's subtree.
    - ``favorites``: True -> only the user's favorites; False -> only non-favorites;
      None -> no favorite filter.
    - ``search``: keep only nodes whose name matches (case-insensitive substring).

    Edges are restricted to the surviving nodes, so a node linked only to
    filtered-out nodes appears isolated. The notes kind filters map onto these
    generic params (journal = ``under=<Journal folder>``; regular = non-favorite
    + ``exclude_descendants_of=<Journal folder>``).
    """
    if scope == "all":
        # accessible_files_q ORs owner with a join to shares, so an owned file
        # with several share rows fans out to duplicate rows; distinct() dedups
        # (the same guard every other accessible_files_q list query applies).
        base = File.objects.filter(
            FileService.accessible_files_q(user), deleted_at__isnull=True
        ).distinct()
    else:
        base = FileService.user_files_qs(user)

    base = base.filter(node_type=File.NodeType.FILE)
    if file_type:
        base = base.filter(type=file_type)
    if under is not None:
        folder_path = _folder_path(user, under)
        base = (
            base.filter(path__startswith=folder_path + "/")
            if folder_path
            else base.none()
        )
    if exclude_descendants_of is not None:
        excl_path = _folder_path(user, exclude_descendants_of)
        if excl_path:
            base = base.exclude(path__startswith=excl_path + "/")
    if favorites is True:
        base = base.filter(favorites__owner=user)
    elif favorites is False:
        base = base.exclude(favorites__owner=user)
    if search:
        base = base.filter(name__icontains=search)
    base = FileService.annotate_for_serializer(base, user)

    # Serialize nodes with the canonical FileSerializer so a graph node is the
    # exact same DTO the rest of the API returns for a file (uuid, type, icon,
    # is_favorite, parent, ...). annotate_for_serializer already supplied the
    # annotations the serializer requires, so this stays N+1-free. Imported
    # lazily: serializers imports FileService, so a module-level import here
    # would be circular.
    from ..serializers import FileSerializer

    files = list(base)
    nodes = FileSerializer(files, many=True).data

    node_uuids = [f.uuid for f in files]
    edges = [
        {"source": str(s), "target": str(t)}
        for s, t in FileLink.objects.filter(
            source_id__in=node_uuids, target_id__in=node_uuids
        ).values_list("source_id", "target_id")
    ]
    return {"nodes": nodes, "edges": edges}

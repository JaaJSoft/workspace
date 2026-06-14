"""Build a node/edge graph of files and their FileLink references.

Generic over file type: ``build_file_graph`` returns the files a user can see
(within a scope) as nodes, and the FileLink rows whose endpoints are BOTH in
that node set as edges. The notes "graph view" calls it with file_type="markdown";
journal coloring is computed by the notes frontend, not here.
"""

from __future__ import annotations

from ..models import File, FileLink
from . import FileService


def build_file_graph(user, *, scope="mine", file_type=None) -> dict:
    """Return ``{"nodes": [...], "edges": [...]}`` for *user* in *scope*.

    scope="mine": the user's own personal (non-deleted) files.
    scope="all":  every non-deleted file the user can access (owned/group/shared).
    Unknown scopes fall back to "mine" (the view validates and 400s first).
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
    base = FileService.annotate_for_serializer(base, user)

    rows = list(
        base.values("uuid", "name", "type", "category", "is_favorite", "parent")
    )
    nodes = [
        {
            "id": str(r["uuid"]),
            "name": r["name"],
            "type": r["type"],
            "category": r["category"],
            "is_favorite": r["is_favorite"],
            "parent": str(r["parent"]) if r["parent"] else None,
        }
        for r in rows
    ]

    node_uuids = [r["uuid"] for r in rows]
    edges = [
        {"source": str(s), "target": str(t)}
        for s, t in FileLink.objects.filter(
            source_id__in=node_uuids, target_id__in=node_uuids
        ).values_list("source_id", "target_id")
    ]
    return {"nodes": nodes, "edges": edges}

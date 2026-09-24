from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.files.services.search_index import FILES_FTS, match_type_for


def reachable_parent_ids(user, nodes):
    """Ids of the parent folders of *nodes* that *user* can open.

    A share reaches the file alone, never its ancestors, and a group file's
    ``group`` says nothing about the folder above it, so the only reliable
    answer is the access check on the parent itself.
    """
    parent_ids = {n.parent_id for n in nodes if n.parent_id}
    if not parent_ids:
        return set()
    return set(
        File.objects.filter(pk__in=parent_ids)
        .filter(pk__in=FileService.accessible_file_ids(user, include_deleted=False))
        .values_list("pk", flat=True)
    )


def in_browsable_tree(f, user, reachable_parents):
    """Whether *user* can browse the folder that holds *f*."""
    if f.parent_id is None:
        return f.group_id is None and f.owner_id == user.id
    return f.parent_id in reachable_parents


def search_files(query, user, limit):
    qs = apply_fulltext(
        exclude_blocked(
            File.objects.filter(
                pk__in=FileService.accessible_file_ids(user, include_deleted=False)
            ).select_related("parent")
        ),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]
    hits = list(qs)
    reachable_parents = reachable_parent_ids(user, hits)
    results = []
    for f in hits:
        browsable = in_browsable_tree(f, user, reachable_parents)
        tags = ()
        if f.node_type == File.NodeType.FOLDER:
            url = f"/files/{f.uuid}"
            type_icon = f.icon or "folder"
        elif browsable:
            # Land in the file's parent folder (path) and open its viewer
            # (?open=), so a search hit reveals the file in context rather
            # than dropping the user at the folder listing.
            folder = f"/files/{f.parent_id}" if f.parent_id else "/files"
            url = f"{folder}?open={f.uuid}"
            type_icon = "file"
        else:
            url = f"/files?shared=1&open={f.uuid}"
            type_icon = "file"
            tags = (SearchTag("Shared with me", "info"),)

        if not tags and f.parent and browsable:
            tags = (SearchTag(f.parent.name, "primary"),)

        results.append(
            SearchResult(
                uuid=str(f.uuid),
                name=f.name,
                url=url,
                matched_value=f.name,
                match_type=match_type_for(f.name, query),
                type_icon=type_icon,
                module_slug="files",
                tags=tags,
            )
        )
    return results

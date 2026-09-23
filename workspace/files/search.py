from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.files.services.search_index import FILES_FTS, match_type_for


def in_browsable_tree(f, user, group_ids):
    """Whether *user* can browse the folder that holds *f*.

    A share reaches the file alone, never its ancestors, so a file only
    shared with the user has no folder to land in.
    """
    if f.group_id is not None:
        return f.group_id in group_ids
    return f.owner_id == user.id


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
    group_ids = set(user.groups.values_list("id", flat=True))
    results = []
    for f in qs:
        tags = ()
        if f.node_type == File.NodeType.FOLDER:
            url = f"/files/{f.uuid}"
            type_icon = f.icon or "folder"
        elif in_browsable_tree(f, user, group_ids):
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

        if not tags and f.parent:
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

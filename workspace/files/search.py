from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.files.services.search_index import FILES_FTS, match_type_for


def search_files(query, user, limit):
    qs = apply_fulltext(
        exclude_blocked(FileService.user_files_qs(user).select_related("parent")),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]
    results = []
    for f in qs:
        if f.node_type == File.NodeType.FOLDER:
            url = f"/files/{f.uuid}"
            type_icon = f.icon or "folder"
        else:
            # Land in the file's parent folder (path) and open its viewer
            # (?open=), so a search hit reveals the file in context rather
            # than dropping the user at the folder listing.
            folder = f"/files/{f.parent_id}" if f.parent_id else "/files"
            url = f"{folder}?open={f.uuid}"
            type_icon = "file"

        tags = ()
        if f.parent:
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
                module_color="primary",
                tags=tags,
            )
        )
    return results

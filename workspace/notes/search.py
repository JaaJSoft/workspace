from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.services import FileService
from workspace.files.services.search_index import FILES_FTS, match_type_for


def search_notes(query, user, limit):
    qs = apply_fulltext(
        FileService.user_files_qs(user)
        .select_related("parent")
        .filter(mime_type="text/markdown"),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]
    results = []
    for f in qs:
        tags = ()
        if f.parent:
            tags = (SearchTag(f.parent.name, "success"),)

        results.append(
            SearchResult(
                uuid=str(f.uuid),
                name=f.name,
                url=f"/notes?file={f.uuid}",
                matched_value=f.name,
                match_type=match_type_for(f.name, query),
                type_icon="notebook-pen",
                module_slug="notes",
                module_color="success",
                tags=tags,
            )
        )
    return results

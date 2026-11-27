from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchScope, SearchTag
from workspace.files.models import File
from workspace.files.queries import accessible_file_ids, user_files_qs
from workspace.files.search import reachable_parent_ids
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.files.services.search_index import FILES_FTS, match_type_for


def search_notes(query, user, limit, scope=SearchScope.ALL):
    if scope == SearchScope.MINE:
        files = user_files_qs(user)
    else:
        files = File.objects.filter(
            pk__in=accessible_file_ids(user, include_deleted=False)
        )
    # Notes are File rows in the same FTS index as the files provider, so the
    # exclusion has to happen here too - and before the slice, or a blocked
    # hit would cost the page a row instead of being replaced by the next one.
    qs = apply_fulltext(
        exclude_blocked(
            files.select_related("parent").filter(mime_type="text/markdown")
        ),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]
    hits = list(qs)
    reachable_parents = reachable_parent_ids(user, hits)
    results = []
    for f in hits:
        tags = ()
        if f.parent_id in reachable_parents:
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
                tags=tags,
            )
        )
    return results

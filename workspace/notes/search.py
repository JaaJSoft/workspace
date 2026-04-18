from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File
from workspace.files.services import FileService


def search_notes(query, user, limit):
    qs = (
        FileService.user_files_qs(user)
        .select_related('parent')
        .filter(mime_type='text/markdown', name__icontains=query)
        .order_by('-updated_at')[:limit]
    )
    results = []
    for f in qs:
        tags = ()
        if f.parent:
            tags = (SearchTag(f.parent.name, 'success'),)

        results.append(SearchResult(
            uuid=str(f.uuid),
            name=f.name,
            url=f'/notes?file={f.uuid}',
            matched_value=f.name,
            match_type='name',
            type_icon='notebook-pen',
            module_slug='notes',
            module_color='success',
            tags=tags,
        ))
    return results

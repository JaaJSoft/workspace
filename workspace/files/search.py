from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.models import File


def search_files(query, user, limit):
    qs = (
        File.objects
        .select_related('parent')
        .filter(owner=user, deleted_at__isnull=True, name__icontains=query)
        .order_by('-updated_at')[:limit]
    )
    results = []
    for f in qs:
        if f.node_type == File.NodeType.FOLDER:
            url = f'/files/{f.uuid}'
            type_icon = f.icon or 'folder'
        else:
            url = f'/files/{f.parent_id}' if f.parent_id else '/files'
            type_icon = 'file'

        tags = ()
        if f.parent:
            tags = (SearchTag(f.parent.name, 'primary'),)

        results.append(SearchResult(
            uuid=str(f.uuid),
            name=f.name,
            url=url,
            matched_value=f.name,
            match_type='name',
            type_icon=type_icon,
            module_slug='files',
            module_color='primary',
            tags=tags,
        ))
    return results

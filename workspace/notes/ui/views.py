from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.files.models import File, Tag
from workspace.files.services import FileService


def _sidebar_context(user):
    all_folders = list(
        FileService.user_files_qs(user)
        .filter(node_type=File.NodeType.FOLDER)
        .exclude(name='Journal', parent__isnull=True)
        .order_by('name')
        .values('uuid', 'name', 'parent_id', 'icon', 'color')
    )

    # Build tree-ordered list (DFS): parents appear before children
    children_map = {}
    roots = []
    folder_uuids = {f['uuid'] for f in all_folders}
    for f in all_folders:
        pid = f['parent_id']
        if pid is None or pid not in folder_uuids:
            roots.append(f)
        else:
            children_map.setdefault(pid, []).append(f)

    folders = []

    def walk(nodes, depth, ancestors):
        for node in nodes:
            node['depth'] = depth
            node['ancestors'] = ','.join(str(a) for a in ancestors)
            node['has_children'] = node['uuid'] in children_map
            folders.append(node)
            walk(children_map.get(node['uuid'], []), depth + 1,
                 ancestors + [node['uuid']])

    walk(roots, 0, [])

    tags = Tag.objects.filter(owner=user).order_by('name')
    return {'folders': folders, 'tags': tags}


@login_required
@ensure_csrf_cookie
def index(request):
    context = _sidebar_context(request.user)

    if request.headers.get('X-Alpine-Request'):
        return render(request, 'notes/ui/partials/sidebar.html', context)

    view = request.GET.get('view', 'all')
    if view not in ('all', 'recent', 'journal', 'folder', 'tag'):
        view = 'all'
    context['initial_view'] = view
    context['initial_id'] = request.GET.get('folder') or request.GET.get('tag') or ''
    context['initial_file'] = request.GET.get('file', '')

    return render(request, 'notes/ui/notes.html', context)

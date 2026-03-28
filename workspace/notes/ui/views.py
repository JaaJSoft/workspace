from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.files.models import File, Tag
from workspace.files.services import FileService


def _get_root_folders(qs):
    """Get root-level folders with has_children flag, serializable as JSON."""
    child_exists = File.objects.filter(
        parent_id=OuterRef('pk'),
        node_type=File.NodeType.FOLDER,
        deleted_at__isnull=True,
    )
    roots = list(
        qs.filter(parent__isnull=True)
        .annotate(has_children=Exists(child_exists))
        .order_by('name')
        .values('uuid', 'name', 'icon', 'color', 'has_children')
    )
    for f in roots:
        f['uuid'] = str(f['uuid'])
    return roots


def _sidebar_context(user):
    personal_qs = (
        FileService.user_files_qs(user)
        .filter(node_type=File.NodeType.FOLDER)
        .exclude(name='Journal')
    )
    folders = _get_root_folders(personal_qs)

    group_qs = (
        FileService.user_group_files_qs(user)
        .filter(node_type=File.NodeType.FOLDER)
    )
    group_folders = _get_root_folders(group_qs)

    tags = Tag.objects.filter(owner=user).order_by('name')

    # Groups without a root folder yet (for create dialog)
    group_root_ids = File.objects.filter(
        group__in=user.groups.all(),
        parent__isnull=True,
        deleted_at__isnull=True,
        node_type=File.NodeType.FOLDER,
    ).values_list('group_id', flat=True)
    available_groups = user.groups.exclude(
        id__in=group_root_ids
    ).order_by('name')

    return {
        'folders_json': folders,
        'group_folders_json': group_folders,
        'tags': tags,
        'available_groups': available_groups,
    }


@login_required
@ensure_csrf_cookie
def index(request):
    context = _sidebar_context(request.user)

    if request.headers.get('X-Alpine-Request'):
        return render(request, 'notes/ui/partials/sidebar.html', context)

    view = request.GET.get('view', 'all')
    if view not in ('all', 'favorites', 'recent', 'journal', 'folder',
                     'tag', 'group_folder'):
        view = 'all'
    context['initial_view'] = view
    context['initial_id'] = (
        request.GET.get('folder') or request.GET.get('tag') or ''
    )
    context['initial_file'] = request.GET.get('file', '')

    return render(request, 'notes/ui/notes.html', context)

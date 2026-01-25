from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, render

from ..models import File, FileFavorite

RECENT_FILES_LIMIT = getattr(settings, 'RECENT_FILES_LIMIT', 25)


def build_breadcrumbs(folder):
    """Build breadcrumb trail from current folder to root."""
    breadcrumbs = []
    current = folder
    while current:
        breadcrumbs.insert(0, {
            'label': current.name,
            'url': f'/files/{current.uuid}',
            'icon': 'folder',
        })
        current = current.parent
    # Add root "Files" at the beginning
    breadcrumbs.insert(0, {
        'label': 'Files',
        'url': '/files',
        'icon': 'hard-drive',
    })
    return breadcrumbs


def _build_context(request, folder=None, is_trash_view=False):
    current_folder = None
    is_favorites_view = (
        not is_trash_view and
        str(request.GET.get('favorites', '')).lower() in {'1', 'true', 'yes'}
    )
    is_recent_view = (
        not is_trash_view and
        not is_favorites_view and
        str(request.GET.get('recent', '')).lower() in {'1', 'true', 'yes'}
    )
    breadcrumbs = [{'label': 'Files', 'url': '/files', 'icon': 'hard-drive'}]

    if is_trash_view:
        breadcrumbs = [
            {'label': 'Files', 'url': '/files', 'icon': 'hard-drive'},
            {'label': 'Trash', 'icon': 'trash-2'},
        ]
    elif is_favorites_view:
        breadcrumbs = [
            {'label': 'Files', 'url': '/files', 'icon': 'hard-drive'},
            {'label': 'Favorites', 'icon': 'star'},
        ]
    elif is_recent_view:
        breadcrumbs = [
            {'label': 'Files', 'url': '/files', 'icon': 'hard-drive'},
            {'label': 'Recent', 'icon': 'clock'},
        ]
    elif folder:
        current_folder = get_object_or_404(
            File,
            uuid=folder,
            owner=request.user,
            node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        )
        breadcrumbs = build_breadcrumbs(current_folder)

    if is_trash_view:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=False,
        ).filter(
            Q(parent__isnull=True) | Q(parent__deleted_at__isnull=True)
        ).order_by('-deleted_at', 'name')
    elif is_favorites_view:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=True,
            favorites__owner=request.user,
        ).distinct().order_by('-node_type', 'name')
    elif is_recent_view:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=True,
        ).order_by('-updated_at', 'name')
    elif current_folder:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=True,
            parent=current_folder,
        ).order_by('-node_type', 'name')
    else:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=True,
            parent__isnull=True,
        ).order_by('-node_type', 'name')

    favorite_subquery = FileFavorite.objects.filter(
        owner=request.user,
        file_id=OuterRef('pk'),
    )
    nodes = nodes.annotate(is_favorite=Exists(favorite_subquery))
    if is_recent_view:
        nodes = nodes[:RECENT_FILES_LIMIT]

    folder_stats = {
        'file_count': 0,
        'folder_count': 0,
        'total_size': 0,
    }
    for node in nodes:
        if node.node_type == File.NodeType.FILE:
            folder_stats['file_count'] += 1
            folder_stats['total_size'] += node.size or 0
        else:
            folder_stats['folder_count'] += 1

    if is_trash_view:
        page_title = 'Trash'
        current_view_url = '/files/trash'
        empty_title = 'Trash is empty'
        empty_message = 'Items you delete stay here for a while.'
    elif is_favorites_view:
        page_title = 'Favorites'
        current_view_url = '/files?favorites=1'
        empty_title = 'No favorites yet'
        empty_message = 'Star files or folders to see them here.'
    elif is_recent_view:
        page_title = 'Recent'
        current_view_url = '/files?recent=1'
        empty_title = 'No recent files'
        empty_message = 'Files you create or edit will show up here.'
    elif current_folder:
        page_title = current_folder.name
        current_view_url = f'/files/{current_folder.uuid}'
        empty_title = None
        empty_message = None
    else:
        page_title = 'All Files'
        current_view_url = '/files'
        empty_title = None
        empty_message = None

    return {
        'nodes': nodes,
        'current_folder': current_folder,
        'breadcrumbs': breadcrumbs,
        'folder_stats': folder_stats,
        'is_favorites_view': is_favorites_view,
        'is_recent_view': is_recent_view,
        'is_trash_view': is_trash_view,
        'is_root_view': (
            not current_folder and
            not is_favorites_view and
            not is_recent_view and
            not is_trash_view
        ),
        'page_title': page_title,
        'current_view_url': current_view_url,
        'empty_title': empty_title,
        'empty_message': empty_message,
    }


@login_required
def index(request, folder=None):
    """File browser view with optional folder navigation."""
    context = _build_context(request, folder=folder, is_trash_view=False)

    if request.headers.get('X-Alpine-Request'):
        return render(request, 'files/ui/index.html#folder-browser', context)

    return render(request, 'files/ui/index.html', context)


@login_required
def trash(request):
    """Trash view for deleted files and folders."""
    context = _build_context(request, is_trash_view=True)

    if request.headers.get('X-Alpine-Request'):
        return render(request, 'files/ui/index.html#folder-browser', context)

    return render(request, 'files/ui/index.html', context)

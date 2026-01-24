from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404, render

from ..models import File, FileFavorite


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


@login_required
def index(request, folder=None):
    """File browser view with optional folder navigation."""
    current_folder = None
    is_favorites_view = str(request.GET.get('favorites', '')).lower() in {'1', 'true', 'yes'}
    breadcrumbs = [{'label': 'Files', 'url': '/files', 'icon': 'hard-drive'}]

    if is_favorites_view:
        breadcrumbs = [
            {'label': 'Files', 'url': '/files', 'icon': 'hard-drive'},
            {'label': 'Favorites', 'icon': 'star'},
        ]
    elif folder:
        current_folder = get_object_or_404(
            File,
            uuid=folder,
            owner=request.user,
            node_type=File.NodeType.FOLDER
        )
        breadcrumbs = build_breadcrumbs(current_folder)

    # Get files in current folder (folders first, then files)
    if is_favorites_view:
        nodes = File.objects.filter(
            owner=request.user,
            favorites__owner=request.user
        ).distinct().order_by('-node_type', 'name')
    elif current_folder:
        nodes = File.objects.filter(
            owner=request.user,
            parent=current_folder
        ).order_by('-node_type', 'name')  # '-' to reverse order: folder before file
    else:
        nodes = File.objects.filter(
            owner=request.user,
            parent__isnull=True
        ).order_by('-node_type', 'name')  # '-' to reverse order: folder before file

    favorite_subquery = FileFavorite.objects.filter(
        owner=request.user,
        file_id=OuterRef('pk'),
    )
    nodes = nodes.annotate(is_favorite=Exists(favorite_subquery))

    # Calculate folder stats
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

    if is_favorites_view:
        page_title = 'Favorites'
        current_view_url = '/files?favorites=1'
        empty_title = 'No favorites yet'
        empty_message = 'Star files or folders to see them here.'
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

    context = {
        'nodes': nodes,
        'current_folder': current_folder,
        'breadcrumbs': breadcrumbs,
        'folder_stats': folder_stats,
        'is_favorites_view': is_favorites_view,
        'is_root_view': not current_folder and not is_favorites_view,
        'page_title': page_title,
        'current_view_url': current_view_url,
        'empty_title': empty_title,
        'empty_message': empty_message,
    }

    # If Alpine AJAX request, return only the partial
    if request.headers.get('X-Alpine-Request'):
        return render(request, 'files/ui/index.html#folder-browser', context)

    return render(request, 'files/ui/index.html', context)

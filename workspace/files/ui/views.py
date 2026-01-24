from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from ..models import File


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
    breadcrumbs = [{'label': 'Files', 'url': '/files', 'icon': 'hard-drive'}]

    if folder:
        current_folder = get_object_or_404(
            File,
            uuid=folder,
            owner=request.user,
            node_type=File.NodeType.FOLDER
        )
        breadcrumbs = build_breadcrumbs(current_folder)

    # Get files in current folder (folders first, then files)
    if current_folder:
        nodes = File.objects.filter(
            owner=request.user,
            parent=current_folder
        ).order_by('-node_type', 'name')  # '-' to reverse order: folder before file
    else:
        nodes = File.objects.filter(
            owner=request.user,
            parent__isnull=True
        ).order_by('-node_type', 'name')  # '-' to reverse order: folder before file

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

    context = {
        'nodes': nodes,
        'current_folder': current_folder,
        'breadcrumbs': breadcrumbs,
        'folder_stats': folder_stats,
    }

    # If Alpine AJAX request, return only the partial
    if request.headers.get('X-Alpine-Request'):
        return render(request, 'files/ui/index.html#folder-browser', context)

    return render(request, 'files/ui/index.html', context)

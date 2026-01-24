from django.contrib.auth.decorators import login_required
from django.db.models import Sum
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


def get_storage_stats(user):
    """Calculate storage statistics for the user."""
    result = File.objects.filter(
        owner=user,
        node_type=File.NodeType.FILE
    ).aggregate(
        total_size=Sum('size'),
    )
    total_size = result['total_size'] or 0
    file_count = File.objects.filter(
        owner=user,
        node_type=File.NodeType.FILE
    ).count()
    folder_count = File.objects.filter(
        owner=user,
        node_type=File.NodeType.FOLDER
    ).count()
    return {
        'total_size': total_size,
        'file_count': file_count,
        'folder_count': folder_count,
    }


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

    # Get storage stats
    storage_stats = get_storage_stats(request.user)

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
        'storage_stats': storage_stats,
        'folder_stats': folder_stats,
    }

    # If Alpine AJAX request, return only the partial
    if request.headers.get('X-Alpine-Request'):
        return render(request, 'files/ui/index.html#folder-browser', context)

    return render(request, 'files/ui/index.html', context)

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse

from ..models import File, FileFavorite, PinnedFolder
from .viewers import ViewerRegistry

RECENT_FILES_LIMIT = getattr(settings, 'RECENT_FILES_LIMIT', 25)


def build_breadcrumbs(folder):
    """Build breadcrumb trail from current folder to root."""
    breadcrumbs = []
    current = folder
    while current:
        breadcrumbs.insert(0, {
            'label': current.name,
            'url': f'/files/{current.uuid}',
            'icon': current.icon or 'folder',
            'icon_color': current.color or 'text-warning',
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
    pinned_subquery = PinnedFolder.objects.filter(
        owner=request.user,
        folder_id=OuterRef('pk'),
    )
    nodes = nodes.annotate(
        is_favorite=Exists(favorite_subquery),
        is_pinned=Exists(pinned_subquery),
    )
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

    pinned_folders_qs = PinnedFolder.objects.filter(
        owner=request.user,
        folder__deleted_at__isnull=True,
    ).select_related('folder').order_by('position', 'created_at')

    # Annotate pinned folders with is_favorite
    pinned_folder_ids = [p.folder_id for p in pinned_folders_qs]
    if pinned_folder_ids:
        pinned_favorites = {
            f.pk: f.is_favorite
            for f in File.objects.filter(pk__in=pinned_folder_ids).annotate(
                is_favorite=Exists(favorite_subquery)
            )
        }
        for pin in pinned_folders_qs:
            pin.folder.is_favorite = pinned_favorites.get(pin.folder_id, False)

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
        'pinned_folders': pinned_folders_qs,
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


@login_required
def properties(request, uuid):
    """Return file/folder properties partial for the properties modal."""
    from django.db.models import Sum

    file_obj = get_object_or_404(File, uuid=uuid, owner=request.user)

    # Check if favorite
    is_favorite = FileFavorite.objects.filter(owner=request.user, file=file_obj).exists()

    # Check if pinned (folders only)
    is_pinned = False
    if file_obj.node_type == File.NodeType.FOLDER:
        is_pinned = PinnedFolder.objects.filter(owner=request.user, folder=file_obj).exists()

    # For folders, get children count and total size
    children_count = 0
    total_size = 0
    if file_obj.node_type == File.NodeType.FOLDER:
        children = File.objects.filter(parent=file_obj, deleted_at__isnull=True)
        children_count = children.count()
        # Calculate total size recursively (simplified: just direct children for now)
        total_size = File.objects.filter(
            path__startswith=f"{file_obj.path}/",
            owner=request.user,
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        ).aggregate(total=Sum('size'))['total'] or 0

    return render(request, 'files/ui/partials/properties_content.html', {
        'file': file_obj,
        'is_favorite': is_favorite,
        'is_pinned': is_pinned,
        'children_count': children_count,
        'total_size': total_size,
    })


@login_required
def pinned_folders(request):
    """Return pinned folders partial for Alpine AJAX loading."""
    # Get pinned folder IDs first
    pinned_qs = PinnedFolder.objects.filter(
        owner=request.user,
        folder__deleted_at__isnull=True,
    ).select_related('folder').order_by('position', 'created_at')

    # Annotate folders with is_favorite
    folder_ids = [p.folder_id for p in pinned_qs]
    favorite_subquery = FileFavorite.objects.filter(
        owner=request.user,
        file_id=OuterRef('pk'),
    )
    folders_with_favorite = {
        f.pk: f.is_favorite
        for f in File.objects.filter(pk__in=folder_ids).annotate(
            is_favorite=Exists(favorite_subquery)
        )
    }

    # Attach is_favorite to each pinned folder's folder object
    for pin in pinned_qs:
        pin.folder.is_favorite = folders_with_favorite.get(pin.folder_id, False)

    return render(request, 'files/ui/partials/pinned_folders.html', {
        'pinned_folders': pinned_qs,
    })


@login_required
def view_file(request, uuid):
    """
    Render file viewer HTML for a specific file.

    Returns the appropriate viewer HTML based on file MIME type.
    Used by the file viewer modal to load content via Alpine AJAX.
    """
    # Get file and check ownership
    file_obj = get_object_or_404(File, uuid=uuid, owner=request.user, deleted_at__isnull=True)

    # Only files can be viewed
    if file_obj.node_type != File.NodeType.FILE:
        return HttpResponse('<div class="p-8 text-center text-error">This is a folder, not a file.</div>', status=400)

    # Check if viewable
    if not file_obj.is_viewable():
        return HttpResponse(
            f'<div class="p-8 text-center text-error">No viewer available for {file_obj.mime_type}</div>',
            status=400
        )

    # Get appropriate viewer
    ViewerClass = ViewerRegistry.get_viewer(file_obj.mime_type)

    if not ViewerClass:
        return HttpResponse(
            f'<div class="p-8 text-center text-error">No viewer available for {file_obj.mime_type}</div>',
            status=400
        )

    # Render viewer
    viewer = ViewerClass(file_obj)
    html = viewer.render(request)

    return HttpResponse(html)

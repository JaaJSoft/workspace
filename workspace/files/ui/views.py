from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef, Q, Subquery
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.files.services import FilePermission, FileService
from workspace.users.settings_service import get_setting
from .viewers import ViewerRegistry
from ..models import File, FileFavorite, FileShare, FileShareLink, PinnedFolder

RECENT_FILES_LIMIT = getattr(settings, 'RECENT_FILES_LIMIT', 25)


def build_breadcrumbs(folder, user=None):
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

    # Group folders: prepend "Groups" as root
    # Personal folders: prepend user's name as root
    if folder.group_id:
        breadcrumbs.insert(0, {
            'label': 'Groups',
            'icon': 'users',
        })
    else:
        label = user.get_full_name() or user.username if user else 'My Files'
        breadcrumbs.insert(0, {
            'label': label,
            'url': '/files',
            'icon': 'hard-drive',
        })
    return breadcrumbs


def _build_context(request, folder=None, is_trash_view=False):
    current_folder = None
    is_shared_view = (
        not is_trash_view and
        str(request.GET.get('shared', '')).lower() in {'1', 'true', 'yes'}
    )
    is_favorites_view = (
        not is_trash_view and
        not is_shared_view and
        str(request.GET.get('favorites', '')).lower() in {'1', 'true', 'yes'}
    )
    is_recent_view = (
        not is_trash_view and
        not is_favorites_view and
        not is_shared_view and
        str(request.GET.get('recent', '')).lower() in {'1', 'true', 'yes'}
    )
    user_label = request.user.get_full_name() or request.user.username
    files_root = {'label': user_label, 'url': '/files', 'icon': 'hard-drive'}
    breadcrumbs = [files_root]

    SPECIAL_VIEWS = {
        'shared': {'label': 'Shared with me', 'icon': 'share-2'},
        'trash': {'label': 'Trash', 'icon': 'trash-2'},
        'favorites': {'label': 'Favorites', 'icon': 'star'},
        'recent': {'label': 'Recent', 'icon': 'clock'},
    }
    active_special = (
        'shared' if is_shared_view else
        'trash' if is_trash_view else
        'favorites' if is_favorites_view else
        'recent' if is_recent_view else
        None
    )
    if active_special:
        breadcrumbs = [files_root, SPECIAL_VIEWS[active_special]]
    elif folder:
        current_folder = File.objects.filter(
            FileService.accessible_files_q(request.user),
            uuid=folder,
            node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        ).first()
        if not current_folder:
            raise Http404
        breadcrumbs = build_breadcrumbs(current_folder, user=request.user)

    if is_shared_view:
        shared_file_ids = FileShare.objects.filter(
            shared_with=request.user,
        ).values_list('file_id', flat=True)
        nodes = File.objects.filter(
            pk__in=shared_file_ids,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
        ).order_by('name')
    elif is_trash_view:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=False,
        ).filter(
            Q(parent__isnull=True) | Q(parent__deleted_at__isnull=True)
        ).order_by('-deleted_at', 'name')
    elif is_favorites_view:
        nodes = File.objects.filter(
            FileService.accessible_files_q(request.user),
            deleted_at__isnull=True,
            favorites__owner=request.user,
        ).distinct().order_by('-node_type', 'name')
    elif is_recent_view:
        nodes = File.objects.filter(
            owner=request.user,
            deleted_at__isnull=True,
        ).order_by('-updated_at', 'name')
    elif current_folder:
        if current_folder.group_id:
            nodes = File.objects.filter(
                group=current_folder.group,
                deleted_at__isnull=True,
                parent=current_folder,
            ).order_by('-node_type', 'name')
        else:
            nodes = FileService.user_files_qs(request.user).filter(
                parent=current_folder,
            ).order_by('-node_type', 'name')
    else:
        nodes = FileService.user_files_qs(request.user).filter(
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
    is_shared_subquery = FileShare.objects.filter(
        file_id=OuterRef('pk'),
    )
    user_share_subquery = FileShare.objects.filter(
        file_id=OuterRef('pk'),
        shared_with=request.user,
    ).values('permission')[:1]
    nodes = nodes.annotate(
        is_favorite=Exists(favorite_subquery),
        is_pinned=Exists(pinned_subquery),
        is_shared=Exists(is_shared_subquery),
        user_share_permission=Subquery(user_share_subquery),
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

    VIEW_META = {
        'shared': ('Shared with me', '/files?shared=1', 'Nothing shared with you', 'Files others share with you will appear here.'),
        'trash': ('Trash', '/files/trash', 'Trash is empty', 'Items you delete stay here for a while.'),
        'favorites': ('Favorites', '/files?favorites=1', 'No favorites yet', 'Star files or folders to see them here.'),
        'recent': ('Recent', '/files?recent=1', 'No recent files', 'Files you create or edit will show up here.'),
    }
    if active_special:
        page_title, current_view_url, empty_title, empty_message = VIEW_META[active_special]
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

    # Group folders the user has access to
    group_folders = FileService.user_group_files_qs(request.user).filter(
        parent__isnull=True,
        node_type=File.NodeType.FOLDER,
    ).select_related('group').order_by('name')

    # Groups without a folder yet (for "Create group folder" action)
    groups_with_folders = group_folders.values_list('group_id', flat=True)
    available_groups = request.user.groups.exclude(id__in=groups_with_folders).order_by('name')

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

    parent_url = breadcrumbs[-2].get('url', '/files') if len(breadcrumbs) >= 2 else None

    # Determine which sidebar item should be active
    if active_special:
        sidebar_active = active_special
    elif current_folder and current_folder.group_id:
        # Find the group root folder UUID
        group_root = current_folder
        while group_root.parent_id:
            group_root = group_root.parent
        sidebar_active = f'group:{group_root.uuid}'
    elif current_folder:
        # Check if we're inside a pinned folder
        pinned_ids = set(pinned_folder_ids) if pinned_folder_ids else set()
        ancestor = current_folder
        sidebar_active = 'root'
        while ancestor:
            if ancestor.pk in pinned_ids:
                sidebar_active = f'pinned:{ancestor.uuid}'
                break
            ancestor = ancestor.parent
    else:
        sidebar_active = 'root'

    file_prefs = get_setting(request.user, 'files', 'preferences', default={})
    breadcrumb_collapse = file_prefs.get('breadcrumbCollapse', 4) if isinstance(file_prefs, dict) else 4

    return {
        'nodes': nodes,
        'current_folder': current_folder,
        'breadcrumbs': breadcrumbs,
        'folder_stats': folder_stats,
        'is_favorites_view': is_favorites_view,
        'is_recent_view': is_recent_view,
        'is_trash_view': is_trash_view,
        'is_shared_view': is_shared_view,
        'is_root_view': (
            not current_folder and
            not is_favorites_view and
            not is_recent_view and
            not is_trash_view and
            not is_shared_view
        ),
        'page_title': page_title,
        'current_view_url': current_view_url,
        'empty_title': empty_title,
        'empty_message': empty_message,
        'parent_url': parent_url,
        'sidebar_active': sidebar_active,
        'pinned_folders': pinned_folders_qs,
        'breadcrumb_collapse': breadcrumb_collapse,
        'group_folders': group_folders,
        'available_groups': available_groups,
    }


@login_required
@ensure_csrf_cookie
def index(request, folder=None):
    """File browser view with optional folder navigation."""
    context = _build_context(request, folder=folder, is_trash_view=False)

    if request.headers.get('X-Alpine-Request'):
        return render(request, 'files/ui/index.html#folder-browser', context)

    return render(request, 'files/ui/index.html', context)


@login_required
@ensure_csrf_cookie
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

    # Try as owner first, then as shared recipient
    file_obj = File.objects.filter(uuid=uuid, deleted_at__isnull=True).first()
    if not file_obj:
        raise Http404

    perm = FileService.get_permission(request.user, file_obj)
    if perm is None:
        raise Http404
    is_owner = perm >= FilePermission.MANAGE

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
        total_size = File.objects.filter(
            path__startswith=f"{file_obj.path}/",
            owner=file_obj.owner,
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        ).aggregate(total=Sum('size'))['total'] or 0

    # Shares (files only, owner sees full list)
    shares = []
    if is_owner and file_obj.node_type == File.NodeType.FILE:
        shares = list(
            FileShare.objects.filter(file=file_obj)
            .select_related('shared_with')
            .order_by('created_at')
        )

    # Share links (files only, owner sees stats)
    share_links = []
    if is_owner and file_obj.node_type == File.NodeType.FILE:
        share_links = list(
            FileShareLink.objects.filter(file=file_obj).order_by('-created_at')
        )

    PERMISSION_LABELS = {
        FilePermission.VIEW: ('Read only', False),
        FilePermission.WRITE: ('Read & write', True),
        FilePermission.EDIT: ('Full access', True),
    }
    perm_label, perm_is_write = PERMISSION_LABELS.get(perm, (None, False))

    return render(request, 'files/ui/partials/properties_content.html', {
        'file': file_obj,
        'is_owner': is_owner,
        'is_favorite': is_favorite,
        'is_pinned': is_pinned,
        'children_count': children_count,
        'total_size': total_size,
        'shares': shares,
        'permission_label': perm_label,
        'permission_is_write': perm_is_write,
        'share_links': share_links,
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
def group_folders_sidebar(request):
    """Return group folders partial for Alpine AJAX refresh."""
    group_folders = FileService.user_group_files_qs(request.user).filter(
        parent__isnull=True,
        node_type=File.NodeType.FOLDER,
    ).select_related('group').order_by('name')

    groups_with_folders = group_folders.values_list('group_id', flat=True)
    available_groups = request.user.groups.exclude(
        id__in=groups_with_folders
    ).order_by('name')

    return render(request, 'files/ui/partials/group_folders_section.html', {
        'group_folders': group_folders,
        'available_groups': available_groups,
    })


@login_required
def view_file(request, uuid):
    """
    Render file viewer HTML for a specific file.

    Returns the appropriate viewer HTML based on file MIME type.
    Used by the file viewer modal to load content via Alpine AJAX.
    """
    # Get file — allow owner or shared-with user
    file_obj = File.objects.select_related('locked_by').filter(uuid=uuid, deleted_at__isnull=True).first()
    if not file_obj:
        raise Http404

    perm = FileService.get_permission(request.user, file_obj)
    if perm is None:
        raise Http404
    user_can_edit = perm >= FilePermission.WRITE

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

    # Lock info for viewers
    lock_info = None
    if user_can_edit and file_obj.is_locked() and file_obj.locked_by_id != request.user.pk:
        lock_info = {
            'locked_by_username': file_obj.locked_by.username,
            'locked_by_id': file_obj.locked_by.pk,
        }

    # Render viewer with user_can_edit override
    viewer = ViewerClass(file_obj)
    viewer._user_can_edit = user_can_edit
    viewer._lock_info = lock_info
    html = viewer.render(request)

    return HttpResponse(html)


@ensure_csrf_cookie
def shared_file_view(request, token):
    """Public standalone page for viewing a shared file."""
    link = (
        FileShareLink.objects
        .select_related('file', 'created_by')
        .filter(token=token, file__deleted_at__isnull=True)
        .first()
    )
    if not link:
        raise Http404

    if link.is_expired:
        return render(request, 'files/ui/shared_file.html', {
            'expired': True,
            'share_token': token,
        })

    # Password check via query param
    access_token = request.GET.get('access_token', '')
    password_verified = False
    if link.has_password and access_token:
        from django.core import signing
        signer = signing.TimestampSigner(salt='file-share-link')
        try:
            value = signer.unsign(access_token, max_age=3600)
            password_verified = (value == link.token)
        except (signing.BadSignature, signing.SignatureExpired):
            pass

    # Render viewer HTML if accessible
    viewer_html = ''
    if not link.has_password or password_verified:
        from workspace.files.ui.viewers import ViewerRegistry
        ViewerClass = ViewerRegistry.get_viewer(link.file.mime_type) if link.file.mime_type else None
        if ViewerClass:
            viewer = ViewerClass(link.file)
            viewer._user_can_edit = False
            content_url = f'/api/v1/files/shared/{token}/content'
            if access_token and password_verified:
                content_url += f'?access_token={access_token}'
            viewer._content_url = content_url
            viewer_html = viewer.render(request)

    return render(request, 'files/ui/shared_file.html', {
        'share_token': token,
        'file': link.file,
        'link': link,
        'viewer_html': viewer_html,
        'needs_password': link.has_password and not password_verified,
        'expired': False,
        'download_url': f'/api/v1/files/shared/{token}/download' + (f'?access_token={access_token}' if access_token and password_verified else ''),
    })

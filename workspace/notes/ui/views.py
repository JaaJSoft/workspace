from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.files.models import File, Tag
from workspace.files.services import FileService
from workspace.users.settings_service import get_setting, set_setting


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


def _ensure_default_folders(user):
    """Find or create the Notes/Journal folder structure, return updated prefs.

    Runs once per user — subsequent calls are no-ops when both UUIDs are valid.
    Migrates legacy root-level Journal folders into Notes/.
    """
    prefs = get_setting(user, 'notes', 'preferences', default={}) or {}
    user_files = FileService.user_files_qs(user)
    folders = user_files.filter(node_type=File.NodeType.FOLDER)
    changed = False

    # ── Notes folder ──
    notes_folder = None
    if prefs.get('defaultFolderUuid'):
        notes_folder = folders.filter(uuid=prefs['defaultFolderUuid']).first()

    if not notes_folder:
        notes_folder = folders.filter(name='Notes', parent__isnull=True).first()

    if not notes_folder:
        notes_folder = FileService.create_folder(
            user, 'Notes', icon='notebook-pen', color='primary',
        )
        changed = True

    # ── Journal folder (migrate from root if needed) ──
    journal_folder = None
    if prefs.get('journalFolderUuid'):
        journal_folder = folders.filter(uuid=prefs['journalFolderUuid']).first()

    if not journal_folder:
        # Check for legacy root-level Journal
        root_journal = folders.filter(name='Journal', parent__isnull=True).first()
        if root_journal:
            root_journal.parent = notes_folder
            root_journal.save(update_fields=['parent'])
            journal_folder = root_journal
            changed = True

    if not journal_folder:
        journal_folder = folders.filter(
            name='Journal', parent=notes_folder,
        ).first()

    if not journal_folder:
        journal_folder = FileService.create_folder(
            user, 'Journal', parent=notes_folder,
            icon='book-open', color='success',
        )
        changed = True

    # ── Persist UUIDs ──
    default_uuid = str(notes_folder.uuid)
    journal_uuid = str(journal_folder.uuid)

    if (prefs.get('defaultFolderUuid') != default_uuid
            or prefs.get('journalFolderUuid') != journal_uuid):
        prefs['defaultFolderUuid'] = default_uuid
        prefs['journalFolderUuid'] = journal_uuid
        set_setting(user, 'notes', 'preferences', prefs)
        changed = True

    return prefs, changed


@login_required
@ensure_csrf_cookie
def index(request):
    _ensure_default_folders(request.user)
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

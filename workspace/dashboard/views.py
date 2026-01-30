from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import render

from workspace.files.models import File, FileFavorite

INSIGHTS_LIMIT = 6

WORKSPACE_MODULES = [
    {
        'name': 'Files',
        'description': 'Store, organize and share files.',
        'icon': 'hard-drive',
        'color': 'primary',
        'url': '/files',
        'active': True,
    },
    {
        'name': 'Emails',
        'description': 'Send and receive emails.',
        'icon': 'mail',
        'color': 'secondary',
        'url': None,
        'active': False,
    },
    {
        'name': 'Notes',
        'description': 'Write and collaborate on documents.',
        'icon': 'notebook-pen',
        'color': 'accent',
        'url': None,
        'active': False,
    },
    {
        'name': 'Calendar',
        'description': 'Schedule events and reminders.',
        'icon': 'calendar',
        'color': 'info',
        'url': None,
        'active': False,
    },
    {
        'name': 'Tasks',
        'description': 'Track projects and to-dos.',
        'icon': 'check-square',
        'color': 'warning',
        'url': None,
        'active': False,
    },
    {
        'name': 'Polls',
        'description': 'Create surveys and collect responses.',
        'icon': 'bar-chart-3',
        'color': 'error',
        'url': None,
        'active': False,
    },
]


def _get_stats(user):
    base_qs = File.objects.filter(owner=user, deleted_at__isnull=True)
    aggregates = base_qs.aggregate(
        file_count=Count('pk', filter=Q(node_type=File.NodeType.FILE)),
        folder_count=Count('pk', filter=Q(node_type=File.NodeType.FOLDER)),
        total_size=Sum('size', filter=Q(node_type=File.NodeType.FILE)),
        last_updated=Max('updated_at'),
    )
    favorite_count = FileFavorite.objects.filter(
        owner=user,
        file__deleted_at__isnull=True,
    ).count()
    trash_count = File.objects.filter(owner=user, deleted_at__isnull=False).count()

    return {
        'file_count': aggregates['file_count'] or 0,
        'folder_count': aggregates['folder_count'] or 0,
        'total_size': aggregates['total_size'] or 0,
        'last_updated': aggregates['last_updated'],
        'favorite_count': favorite_count,
        'trash_count': trash_count,
    }


def _get_recent_nodes(user, limit=INSIGHTS_LIMIT):
    return File.objects.filter(
        owner=user,
        deleted_at__isnull=True,
    ).select_related('parent').order_by('-updated_at')[:limit]


def _get_favorite_nodes(user, limit=INSIGHTS_LIMIT):
    favorites = FileFavorite.objects.filter(
        owner=user,
        file__deleted_at__isnull=True,
    ).select_related('file', 'file__parent').order_by('-created_at')[:limit]
    return [favorite.file for favorite in favorites]


def _get_trash_nodes(user, limit=INSIGHTS_LIMIT):
    return File.objects.filter(
        owner=user,
        deleted_at__isnull=False,
    ).select_related('parent').order_by('-deleted_at')[:limit]


def _build_dashboard_context(
    user,
    include_stats=True,
    include_recent=True,
    include_favorites=True,
    include_trash=True,
):
    context = {'modules': WORKSPACE_MODULES}
    if include_stats:
        context['stats'] = _get_stats(user)
    if include_recent:
        context['recent_nodes'] = _get_recent_nodes(user)
    if include_favorites:
        context['favorite_nodes'] = _get_favorite_nodes(user)
    if include_trash:
        context['trash_nodes'] = _get_trash_nodes(user)
    return context


def _render_insights(request, tab, template_name):
    if request.headers.get('X-Alpine-Request'):
        context = _build_dashboard_context(
            request.user,
            include_stats=False,
            include_recent=tab == 'recent',
            include_favorites=tab == 'favorites',
            include_trash=tab == 'trash',
        )
        return render(request, template_name, context)

    context = _build_dashboard_context(request.user)
    context['insight_tab'] = tab
    return render(request, 'dashboard/index.html', context)


@login_required
def index(request):
    """Dashboard home page."""
    context = _build_dashboard_context(request.user)
    context['insight_tab'] = 'recent'
    return render(request, 'dashboard/index.html', context)


@login_required
def stats(request):
    if request.headers.get('X-Alpine-Request'):
        context = _build_dashboard_context(
            request.user,
            include_recent=False,
            include_favorites=False,
            include_trash=False,
        )
        return render(request, 'dashboard/partials/stats.html', context)

    context = _build_dashboard_context(request.user)
    context['insight_tab'] = 'recent'
    return render(request, 'dashboard/index.html', context)


@login_required
def insights_recent(request):
    return _render_insights(
        request,
        tab='recent',
        template_name='dashboard/partials/insights_recent.html',
    )


@login_required
def insights_favorites(request):
    return _render_insights(
        request,
        tab='favorites',
        template_name='dashboard/partials/insights_favorites.html',
    )


@login_required
def insights_trash(request):
    return _render_insights(
        request,
        tab='trash',
        template_name='dashboard/partials/insights_trash.html',
    )

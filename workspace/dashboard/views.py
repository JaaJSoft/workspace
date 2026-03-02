from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from workspace.core.activity_service import annotate_time_ago, get_recent_events, get_sources
from workspace.core.module_registry import registry

ACTIVITY_LIMIT = 10


def _get_activity_context(user, source=None):
    """Build activity feed context for templates."""
    events = get_recent_events(
        viewer_id=user.id,
        source=source,
        exclude_user_id=user.id,
        limit=ACTIVITY_LIMIT,
    )
    annotate_time_ago(events)

    return {
        'activity_events': events,
        'activity_sources': get_sources(),
    }


def _build_dashboard_context(user, include_activity=True, activity_source=None):
    pending_action_counts = registry.get_pending_action_counts(user)
    modules = []
    for m in registry.get_for_template():
        if m['slug'] != 'dashboard':
            m['pending_action_count'] = pending_action_counts.get(m['slug'], 0)
            modules.append(m)

    context = {
        'modules': modules,
    }
    if include_activity:
        context.update(_get_activity_context(user, source=activity_source))
    return context


@login_required
def index(request):
    """Dashboard home page."""
    context = _build_dashboard_context(request.user)
    context['activity_tab'] = 'all'
    return render(request, 'dashboard/index.html', context)


@login_required
def activity_feed(request):
    """Activity feed partial (or full page if not Alpine AJAX)."""
    source = request.GET.get('source')
    tab = source or 'all'

    if request.headers.get('X-Alpine-Request'):
        context = _build_dashboard_context(
            request.user,
            include_activity=False,
            activity_source=source,
        )
        context.update(_get_activity_context(request.user, source=source))
        return render(request, 'dashboard/partials/activity_feed.html', context)

    context = _build_dashboard_context(request.user, activity_source=source)
    context['activity_tab'] = tab
    return render(request, 'dashboard/index.html', context)

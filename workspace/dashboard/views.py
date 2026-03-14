from datetime import datetime, time

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from workspace.calendar.upcoming import get_upcoming_for_user
from workspace.core.activity_service import annotate_time_ago, get_recent_events, get_sources
from workspace.core.activity_registry import activity_registry
from workspace.core.module_registry import registry

from django.conf import settings as django_settings

ACTIVITY_LIMIT = 10


def _get_upcoming_events(user):
    """Return today's upcoming events for the user, including recurring."""
    now = timezone.now()
    end_of_today = timezone.make_aware(
        datetime.combine(now.date(), time.max),
        timezone.get_current_timezone(),
    )
    return get_upcoming_for_user(user, now, end_of_today)


def _get_activity_context(user, source=None, offset=0, search=None):
    """Build activity feed context for templates."""
    events = get_recent_events(
        viewer_id=user.id,
        source=source,
        exclude_user_id=user.id,
        search=search,
        limit=ACTIVITY_LIMIT + 1,
        offset=offset,
    )

    has_more = len(events) > ACTIVITY_LIMIT
    events = events[:ACTIVITY_LIMIT]
    annotate_time_ago(events)

    return {
        'activity_events': events,
        'activity_sources': get_sources(),
        'activity_source': source,
        'activity_search': search or '',
        'activity_has_more': has_more,
        'activity_next_offset': offset + ACTIVITY_LIMIT,
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
        'upcoming_events': _get_upcoming_events(user),
        'usage_stats': activity_registry.get_stats(user.id),
        'storage_quota': django_settings.STORAGE_QUOTA_BYTES,
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

    offset = int(request.GET.get('offset', 0))
    search = request.GET.get('q', '').strip() or None
    append = offset > 0

    if request.headers.get('X-Alpine-Request'):
        context = _build_dashboard_context(
            request.user,
            include_activity=False,
            activity_source=source,
        )
        context.update(_get_activity_context(request.user, source=source, offset=offset, search=search))
        template = 'dashboard/partials/activity_page.html' if append else 'dashboard/partials/activity_feed.html'
        return render(request, template, context)

    context = _build_dashboard_context(request.user, activity_source=source)
    context['activity_tab'] = tab
    return render(request, 'dashboard/index.html', context)

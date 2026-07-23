from dataclasses import asdict
from datetime import datetime, time

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from workspace.calendar.upcoming import get_upcoming_for_user
from workspace.core.module_registry import registry
from workspace.core.services.activity import (
    annotate_time_ago,
    get_recent_events,
    get_sources,
    get_usage_stats,
)
from workspace.core.services.module_visibility import visible_modules
from workspace.users.services.settings import get_module_settings, get_setting

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
        visible_to=user,
    )

    has_more = len(events) > ACTIVITY_LIMIT
    events = events[:ACTIVITY_LIMIT]
    annotate_time_ago(events)

    return {
        "activity_events": events,
        "activity_sources": get_sources(user),
        "activity_source": source,
        "activity_search": search or "",
        "activity_has_more": has_more,
        "activity_next_offset": offset + ACTIVITY_LIMIT,
        "activity_prefix": "dashboard-activity",
        "activity_base_url": reverse("dashboard:activity_feed"),
    }


def _activity_shell_context(user, source=None):
    """Cheap activity context for the dashboard shell - no feed query.

    The feed itself is fetched asynchronously (see ``activity_feed``); the
    initial render only needs the source tabs and the URLs/prefix to wire the
    alpine-ajax swap. ``get_sources()`` reads in-memory registry metadata, so
    this adds no database cost to the page load.
    """
    return {
        "activity_sources": get_sources(user),
        "activity_source": source,
        "activity_search": "",
        "activity_prefix": "dashboard-activity",
        "activity_base_url": reverse("dashboard:activity_feed"),
    }


def _dashboard_modules(user):
    """Build the dashboard app tiles and the settings-popover app list.

    Returns ``(modules, dashboard_apps)`` where ``modules`` is the visible grid
    (hidden slugs and the dashboard tile excluded, pending counts attached) and
    ``dashboard_apps`` is every visible app with a ``hidden`` flag for the
    settings popover.
    """
    pending_action_counts = registry.get_pending_action_counts(user)
    hidden = set(get_module_settings(user, "dashboard").get("hidden_modules") or [])
    modules = []
    dashboard_apps = []
    for m in visible_modules(user):
        if m.slug == "dashboard":
            continue
        dashboard_apps.append(
            {
                "slug": m.slug,
                "name": m.name,
                "icon": m.icon,
                "color": m.color,
                "hidden": m.slug in hidden,
            }
        )
        if m.slug in hidden:
            continue
        data = asdict(m)
        data["pending_action_count"] = pending_action_counts.get(m.slug, 0)
        modules.append(data)
    return modules, dashboard_apps


def _build_dashboard_context(user, include_activity=True, activity_source=None):
    dashboard_settings = get_module_settings(user, "dashboard")
    modules, dashboard_apps = _dashboard_modules(user)

    context = {
        "modules": modules,
        "dashboard_apps": dashboard_apps,
        "show_upcoming_events": dashboard_settings.get("show_upcoming_events", True),
        "show_upcoming_empty": dashboard_settings.get("show_upcoming_empty", True),
        "usage_stats": get_usage_stats(user.id),
        "storage_quota": django_settings.STORAGE_QUOTA_BYTES,
    }
    if include_activity:
        context.update(_get_activity_context(user, source=activity_source))
    return context


@login_required
def index(request):
    """Dashboard home page.

    The activity feed loads asynchronously (see ``activity_feed``), so the
    initial render stays off the heavy per-provider feed fan-out and the page
    paints immediately.
    """
    context = _build_dashboard_context(request.user, include_activity=False)
    context.update(_activity_shell_context(request.user))
    context["activity_tab"] = "all"
    return render(request, "dashboard/index.html", context)


@login_required
def modules_fragment(request):
    """Dashboard app grid, re-rendered after a visibility change (alpine-ajax swap)."""
    modules, _ = _dashboard_modules(request.user)
    return render(
        request,
        "dashboard/partials/modules_grid_fragment.html",
        {"modules": modules},
    )


@login_required
def upcoming_fragment(request):
    """Dashboard upcoming-events widget, loaded async via alpine-ajax."""
    return render(
        request,
        "dashboard/partials/upcoming_events.html",
        {
            "upcoming_events": _get_upcoming_events(request.user),
            "show_upcoming_empty": get_setting(
                request.user,
                "dashboard",
                "show_upcoming_empty",
                default=True,
            ),
        },
    )


@login_required
def activity_feed(request):
    """Activity feed partial (or full page if not Alpine AJAX)."""
    source = request.GET.get("source")
    tab = source or "all"

    offset = int(request.GET.get("offset", 0))
    search = request.GET.get("q", "").strip() or None
    append = offset > 0

    if request.headers.get("X-Alpine-Request"):
        # Only the feed partial is rendered here - it uses none of the
        # dashboard shell context (modules / pending counts / usage stats),
        # so we skip recomputing them on every feed fetch.
        context = _get_activity_context(
            request.user, source=source, offset=offset, search=search
        )
        template = (
            "ui/partials/activity_page.html"
            if append
            else "ui/partials/activity_feed.html"
        )
        return render(request, template, context)

    # Direct full-page navigation: render the shell; the feed loads async.
    context = _build_dashboard_context(request.user, include_activity=False)
    context.update(_activity_shell_context(request.user, source=source))
    context["activity_tab"] = tab
    return render(request, "dashboard/index.html", context)

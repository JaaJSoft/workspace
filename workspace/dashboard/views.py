from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from workspace.common.prefetch import prefetchable
from workspace.core.services.activity import (
    allowed_activity_sources,
    annotate_time_ago,
    get_recent_events,
    get_sources,
    get_usage_headlines,
)
from workspace.core.today_registry import today_registry
from workspace.files.services.quota import (
    effective_quota,
    personal_usage,
    usage_percent,
)
from workspace.users.services.settings import get_module_settings

from .services.modules import dashboard_modules
from .services.preferences import (
    TEMPERATURE_UNITS,
    hidden_today_sources,
    temperature_unit,
)

ACTIVITY_LIMIT = 10
# Rows the Today card shows before its show-more button.
TODAY_LIMIT = 8


def _today_sources(user, dashboard_settings):
    """The Today sources *user* can see, each flagged ``hidden`` or not."""
    hidden = hidden_today_sources(dashboard_settings)
    return [
        (source, source.slug in hidden)
        for source in today_registry.visible_sources(user)
    ]


def _today_list(user, sources):
    """The card's rows, urgent first, and one filter per source with rows.

    Sorting is stable, so outside the urgent ones the rows keep their
    source's order and, within a source, the order the source gave them.
    """
    now = timezone.now()
    tz = timezone.get_current_timezone()
    rows, filters = [], []
    for source in sources:
        result = source.items_fn(user, now, tz)
        if not result.items:
            continue
        rows.extend(
            {"source": source, "item": item, "urgent": source.is_urgent(item)}
            for item in result.items
        )
        filters.append(
            {
                "source": source,
                "total": result.total,
                "more": result.total - len(result.items),
            }
        )
    rows.sort(key=lambda row: not row["urgent"])
    return rows, filters


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


def _build_dashboard_context(user, include_activity=True, activity_source=None):
    dashboard_settings = get_module_settings(user, "dashboard")
    modules, dashboard_apps = dashboard_modules(user)

    today_sources = _today_sources(user, dashboard_settings)

    storage_usage = personal_usage(user.pk)
    storage_quota = effective_quota(user.pk)

    context = {
        "modules": modules,
        "dashboard_apps": dashboard_apps,
        "today_sources": [
            {
                "slug": source.slug,
                "label": str(source.label),
                "description": str(source.description),
                "icon": source.icon,
            }
            for source, _ in today_sources
        ],
        "hidden_today_sources": [
            source.slug for source, hidden in today_sources if hidden
        ],
        "show_today": any(not hidden for _, hidden in today_sources),
        "temperature_unit": temperature_unit(dashboard_settings),
        "temperature_units": TEMPERATURE_UNITS.items(),
        "usage_rows": get_usage_headlines(user),
        "storage_usage": storage_usage,
        "storage_quota": storage_quota,
        "storage_percent": usage_percent(storage_usage, storage_quota),
    }
    if include_activity:
        context.update(_get_activity_context(user, source=activity_source))
    return context


@prefetchable
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
    modules, _ = dashboard_modules(request.user)
    return render(
        request,
        "dashboard/partials/modules_grid_fragment.html",
        {"modules": modules},
    )


@login_required
def today_fragment(request):
    """The Today card, loaded after the page via alpine-ajax.

    With every source switched off the response is the empty hidden slot:
    the preferences swap the card live in both directions, so the target id
    must always render.
    """
    sources = [
        source
        for source, hidden in _today_sources(
            request.user, get_module_settings(request.user, "dashboard")
        )
        if not hidden
    ]
    context = {"show_today": bool(sources)}
    if sources:
        rows, filters = _today_list(request.user, sources)
        context.update(
            today_rows=rows,
            today_filters=filters,
            today_urgent=sum(row["urgent"] for row in rows),
            today_total=sum(f["total"] for f in filters),
            today_limit=TODAY_LIMIT,
            today_more=max(len(rows) - TODAY_LIMIT, 0),
        )
    return render(request, "dashboard/partials/today.html", context)


@login_required
def activity_feed(request):
    """Activity feed partial (or full page if not Alpine AJAX)."""
    source = request.GET.get("source")
    if source not in allowed_activity_sources(request.user):
        source = None
    tab = source or "all"

    try:
        offset = max(0, int(request.GET.get("offset", 0)))
    except TypeError, ValueError:
        offset = 0
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
            "core/partials/activity_page.html"
            if append
            else "core/partials/activity_feed.html"
        )
        return render(request, template, context)

    # Direct full-page navigation: render the shell; the feed loads async.
    context = _build_dashboard_context(request.user, include_activity=False)
    context.update(_activity_shell_context(request.user, source=source))
    context["activity_tab"] = tab
    return render(request, "dashboard/index.html", context)

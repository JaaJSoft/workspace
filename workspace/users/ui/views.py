from datetime import date as date_type, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from workspace.core.activity_registry import activity_registry
from workspace.core.services.activity import annotate_time_ago, get_recent_events, get_sources
from workspace.users.banner_palettes import BANNER_PALETTES, gradient_from_palette_value
from workspace.users.services import avatar as avatar_service, presence as presence_service
from workspace.users.services.settings import get_module_settings

ACTIVITY_LIMIT = 10


def _build_heatmap_data(user_id, viewer_id=None):
    """Build contribution heatmap grid data for the last 12 months."""
    today = timezone.now().date()
    # Go back ~12 months (52 weeks)
    # Start from the Monday of the week 51 weeks ago
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=51)
    date_from = start
    date_to = today

    counts = activity_registry.get_daily_counts(
        user_id, date_from, date_to, viewer_id=viewer_id,
    )

    # Compute quantile thresholds from non-zero values
    nonzero = sorted(v for v in counts.values() if v > 0)
    if nonzero:
        q1 = nonzero[len(nonzero) // 4] if len(nonzero) >= 4 else 1
        q2 = nonzero[len(nonzero) // 2] if len(nonzero) >= 2 else q1 + 1
        q3 = nonzero[3 * len(nonzero) // 4] if len(nonzero) >= 4 else q2 + 1
    else:
        q1, q2, q3 = 1, 2, 3

    def level(count):
        if count == 0:
            return 0
        if count <= q1:
            return 1
        if count <= q2:
            return 2
        if count <= q3:
            return 3
        return 4

    # Build weeks (columns), each week has 7 days (rows: Mon=0 ... Sun=6)
    weeks = []
    current = start
    week = []
    while current <= date_to:
        c = counts.get(current, 0)
        week.append({
            'date': current.isoformat(),
            'count': c,
            'level': level(c),
        })
        if len(week) == 7:
            weeks.append(week)
            week = []
        current += timedelta(days=1)
    if week:
        weeks.append(week)

    # Month labels: label only the first week that contains a new month
    month_labels = []
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    last_labeled_month = None
    for w in weeks:
        label = ''
        for day in w:
            d = date_type.fromisoformat(day['date'])
            if d.day <= 7 and d.month != last_labeled_month:
                label = month_names[d.month - 1]
                last_labeled_month = d.month
                break
        month_labels.append(label)

    total_contributions = sum(counts.values())

    return {
        'weeks': weeks,
        'month_labels': month_labels,
        'total_contributions': total_contributions,
    }


def _get_profile_activity_context(username, user_id, viewer_id=None, source=None, offset=0, search=None):
    """Build activity feed context for the profile page."""
    events = get_recent_events(
        user_id=user_id,
        viewer_id=viewer_id,
        source=source,
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
        'activity_prefix': 'profile-activity',
        'activity_base_url': reverse('users_ui:profile_activity_feed', kwargs={'username': username}),
    }


@login_required
def profile_view(request, username=None):
    if username is None:
        profile_user = request.user
    else:
        try:
            profile_user = User.objects.get(username=username, is_active=True, bot_profile__isnull=True)
        except User.DoesNotExist:
            raise Http404

    is_own_profile = profile_user == request.user
    user_id = profile_user.id
    viewer_id = None if is_own_profile else request.user.id

    # Activity stats
    stats = activity_registry.get_stats(user_id, viewer_id=viewer_id)

    # Heatmap
    heatmap = _build_heatmap_data(user_id, viewer_id=viewer_id)

    # Profile fields — single query for all three keys instead of one per get_setting
    profile_settings = get_module_settings(profile_user, 'profile')
    profile_bio = profile_settings.get('bio')
    profile_role = profile_settings.get('role')
    banner_gradient = gradient_from_palette_value(profile_settings.get('banner_palette'))

    # Activity feed
    activity_ctx = _get_profile_activity_context(profile_user.username, user_id, viewer_id=viewer_id)

    context = {
        'profile_user': profile_user,
        'is_own_profile': is_own_profile,
        'last_seen': presence_service.get_last_seen(user_id),
        'activity_stats': stats,
        'heatmap': heatmap,
        'profile_bio': profile_bio,
        'profile_role': profile_role,
        'banner_gradient': banner_gradient,
    }
    context.update(activity_ctx)
    return render(request, 'users/ui/profile.html', context)


@login_required
def profile_activity_feed(request, username):
    """Activity feed partial for Alpine AJAX load-more on profile page."""
    try:
        profile_user = User.objects.get(username=username, is_active=True, bot_profile__isnull=True)
    except User.DoesNotExist:
        raise Http404

    is_own_profile = profile_user == request.user
    viewer_id = None if is_own_profile else request.user.id

    source = request.GET.get('source')
    search = request.GET.get('q', '').strip() or None
    try:
        offset = int(request.GET.get('offset', 0))
    except (TypeError, ValueError):
        offset = 0

    activity_ctx = _get_profile_activity_context(
        username, profile_user.id, viewer_id=viewer_id, source=source, offset=offset, search=search,
    )
    activity_ctx['profile_user'] = profile_user

    append = offset > 0
    if request.headers.get('X-Alpine-Request'):
        template = 'ui/partials/activity_page.html' if append else 'ui/partials/activity_feed.html'
        return render(request, template, activity_ctx)

    return redirect('users_ui:profile_by_username', username=username)


@login_required
def settings_view(request):
    from django.conf import settings as django_settings
    # Batch reads per module — 2 queries instead of one per key.
    profile_settings = get_module_settings(request.user, 'profile')
    dashboard_settings = get_module_settings(request.user, 'dashboard')
    return render(request, 'users/ui/settings.html', {
        'has_avatar': avatar_service.has_avatar(request.user),
        'usage_stats': activity_registry.get_stats(request.user.id),
        'storage_quota': django_settings.STORAGE_QUOTA_BYTES,
        'profile_bio': profile_settings.get('bio') or '',
        'profile_role': profile_settings.get('role') or '',
        'banner_palette': profile_settings.get('banner_palette'),
        'banner_palettes': BANNER_PALETTES,
        'show_upcoming_events': dashboard_settings.get('show_upcoming_events', True),
        'show_upcoming_empty': dashboard_settings.get('show_upcoming_empty', True),
    })


@login_required
def user_card_view(request, user_id):
    try:
        card_user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        raise Http404
    card_status = presence_service.get_status(card_user.id)
    ctx = {
        'card_user': card_user,
        'card_user_status': card_status,
    }
    if card_status != 'online':
        ctx['card_user_last_seen'] = presence_service.get_last_seen(card_user.id)
    return render(request, 'users/ui/partials/user_card.html', ctx)

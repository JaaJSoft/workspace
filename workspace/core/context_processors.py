from dataclasses import asdict

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from workspace.core.changelog import get_latest_version
from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import (
    current_module,
    filter_visible_commands,
    visible_modules,
)
from workspace.core.setting_keys import (
    CHANGELOG_LAST_SEEN_VERSION,
    MODULE,
    ONBOARDING_COMPLETED,
    SIDEBAR_COLLAPSED,
)
from workspace.dashboard.services.modules import switcher_modules_for
from workspace.users.services.settings import get_module_settings, get_setting


def workspace_modules(request):
    onboarding_pending = False
    changelog_unread = False
    if request.user.is_authenticated:
        # Both keys live in the core module; fetch them in a single query
        # (shared with the user_preferences context processor via the cache).
        core_settings = get_module_settings(request.user, MODULE)
        onboarding_pending = not core_settings.get(ONBOARDING_COMPLETED, False)
        if not onboarding_pending:
            latest = get_latest_version()
            if latest:
                last_seen = core_settings.get(CHANGELOG_LAST_SEEN_VERSION)
                changelog_unread = last_seen != latest

    modules = visible_modules(request.user)
    current = current_module(modules, request.path)
    # Goes into the <aside>'s class attribute: Alpine binds only after the
    # parse, so the first paint needs the sidebar's final width in the HTML.
    sidebar_collapsed = False
    if current and request.user.is_authenticated:
        sidebar_collapsed = (
            get_setting(request.user, current.slug, SIDEBAR_COLLAPSED, default=False)
            is True
        )
    # Rendered with the page (no fetch on open); lazy so the unread-badge
    # query only runs when a template actually iterates the switcher, never
    # on a fragment render that draws no navbar.
    switcher_modules = []
    if request.user.is_authenticated:
        switcher_modules = SimpleLazyObject(
            lambda: switcher_modules_for(request.user, current)
        )
    return {
        "workspace_active_modules": [asdict(m) for m in modules],
        "workspace_current_module": asdict(current) if current else None,
        "workspace_sidebar_collapsed": sidebar_collapsed,
        "workspace_switcher_modules": switcher_modules,
        "workspace_commands": [
            asdict(c)
            for c in filter_visible_commands(
                request.user, registry.get_active_commands()
            )
        ],
        "APP_VERSION": settings.APP_VERSION,
        "CHANGELOG_UNREAD": changelog_unread,
        "ONBOARDING_PENDING": onboarding_pending,
    }

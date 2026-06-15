from dataclasses import asdict

from django.conf import settings

from workspace.core.changelog import get_latest_version
from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import (
    filter_visible_commands,
    visible_modules,
)
from workspace.core.setting_keys import (
    CHANGELOG_LAST_SEEN_VERSION,
    MODULE,
    ONBOARDING_COMPLETED,
)
from workspace.users.services.settings import get_module_settings


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

    return {
        "workspace_active_modules": [asdict(m) for m in visible_modules(request.user)],
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

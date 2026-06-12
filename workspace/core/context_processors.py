from dataclasses import asdict

from django.conf import settings

from workspace.core.changelog import get_latest_version
from workspace.core.module_registry import registry
from workspace.core.services.module_access import (
    enabled_module_slugs,
    restrictable_module_slugs,
)
from workspace.core.setting_keys import (
    CHANGELOG_LAST_SEEN_VERSION,
    MODULE,
    ONBOARDING_COMPLETED,
)
from workspace.users.services.settings import get_setting


def workspace_modules(request):
    onboarding_pending = False
    changelog_unread = False
    if request.user.is_authenticated:
        onboarding_pending = not get_setting(
            request.user,
            MODULE,
            ONBOARDING_COMPLETED,
            default=False,
        )
        if not onboarding_pending:
            latest = get_latest_version()
            if latest:
                last_seen = get_setting(
                    request.user,
                    MODULE,
                    CHANGELOG_LAST_SEEN_VERSION,
                )
                changelog_unread = last_seen != latest

    active_modules = registry.get_active()
    active_commands = registry.get_active_commands()
    if request.user.is_authenticated:
        enabled = enabled_module_slugs(request.user)
        restrictable = restrictable_module_slugs()
        active_modules = [
            m for m in active_modules if m.slug not in restrictable or m.slug in enabled
        ]
        active_commands = [
            c
            for c in active_commands
            if c.module_slug not in restrictable or c.module_slug in enabled
        ]

    return {
        "workspace_active_modules": [asdict(m) for m in active_modules],
        "workspace_commands": [asdict(c) for c in active_commands],
        "APP_VERSION": settings.APP_VERSION,
        "CHANGELOG_UNREAD": changelog_unread,
        "ONBOARDING_PENDING": onboarding_pending,
    }

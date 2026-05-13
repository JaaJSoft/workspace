from dataclasses import asdict

from django.conf import settings

from workspace.core.changelog import get_latest_version
from workspace.core.module_registry import registry
from workspace.core.views_changelog import (
    CHANGELOG_SETTING_KEY,
    CHANGELOG_SETTING_MODULE,
)
from workspace.users.services.settings import get_setting


def workspace_modules(request):
    changelog_unread = False
    if request.user.is_authenticated:
        latest = get_latest_version()
        if latest:
            last_seen = get_setting(
                request.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY,
            )
            changelog_unread = last_seen != latest

    return {
        'workspace_active_modules': [asdict(m) for m in registry.get_active()],
        'workspace_commands': [asdict(c) for c in registry.get_active_commands()],
        'APP_VERSION': settings.APP_VERSION,
        'CHANGELOG_UNREAD': changelog_unread,
    }

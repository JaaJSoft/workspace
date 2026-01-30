"""Convenience helpers for reading / writing user settings from any module.

Usage from another app::

    from workspace.users.settings_service import get_setting, set_setting

    theme = get_setting(request.user, 'core', 'theme', default='light')
    set_setting(request.user, 'core', 'theme', 'dark')
"""

from __future__ import annotations

from typing import Any

from workspace.users.models import UserSetting


def get_setting(user, module: str, key: str, *, default: Any = None) -> Any:
    """Return the value of a single setting, or *default* if it does not exist."""
    try:
        return UserSetting.objects.values_list('value', flat=True).get(
            user=user, module=module, key=key,
        )
    except UserSetting.DoesNotExist:
        return default


def set_setting(user, module: str, key: str, value: Any) -> UserSetting:
    """Create or update a setting and return the model instance."""
    obj, _ = UserSetting.objects.update_or_create(
        user=user, module=module, key=key,
        defaults={'value': value},
    )
    return obj


def delete_setting(user, module: str, key: str) -> bool:
    """Delete a setting. Return ``True`` if it existed."""
    deleted, _ = UserSetting.objects.filter(
        user=user, module=module, key=key,
    ).delete()
    return deleted > 0


def get_module_settings(user, module: str) -> dict[str, Any]:
    """Return all settings for a given module as a ``{key: value}`` dict."""
    qs = UserSetting.objects.filter(user=user, module=module).values_list('key', 'value')
    return dict(qs)


def get_all_settings(user) -> list[dict]:
    """Return every setting for *user* as a list of dicts."""
    return list(
        UserSetting.objects.filter(user=user).values('module', 'key', 'value')
    )

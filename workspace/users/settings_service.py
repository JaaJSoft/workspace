"""Convenience helpers for reading / writing user settings from any module.

Usage from another app::

    from workspace.users.settings_service import get_setting, set_setting

    theme = get_setting(request.user, 'core', 'theme', default='light')
    set_setting(request.user, 'core', 'theme', 'dark')
"""

from __future__ import annotations

from datetime import timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.cache import cache

from workspace.users.models import UserSetting

UTC = ZoneInfo('UTC')

_CACHE_TTL = 300  # 5 minutes
_SENTINEL = object()  # distinguishes "not in cache" from any cached value
_DB_MISS = '__SETTING_NOT_FOUND__'


def _cache_key(user_id: int, module: str, key: str) -> str:
    return f'usetting:{user_id}:{module}:{key}'


def get_user_timezone(user) -> ZoneInfo:
    """Return the user's configured timezone, or UTC if unset/invalid."""
    tz_name = get_setting(user, 'core', 'timezone')
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return UTC


def get_setting(user, module: str, key: str, *, default: Any = None) -> Any:
    """Return the value of a single setting, or *default* if it does not exist."""
    ck = _cache_key(user.pk, module, key)
    cached = cache.get(ck, _SENTINEL)
    if cached is not _SENTINEL:
        return default if cached == _DB_MISS else cached
    try:
        value = UserSetting.objects.get(user=user, module=module, key=key).value
    except UserSetting.DoesNotExist:
        cache.set(ck, _DB_MISS, _CACHE_TTL)
        return default
    cache.set(ck, value, _CACHE_TTL)
    return value


def set_setting(user, module: str, key: str, value: Any) -> UserSetting:
    """Create or update a setting and return the model instance."""
    obj, _ = UserSetting.objects.update_or_create(
        user=user, module=module, key=key,
        defaults={'value': value},
    )
    cache.set(_cache_key(user.pk, module, key), value, _CACHE_TTL)
    return obj


def delete_setting(user, module: str, key: str) -> bool:
    """Delete a setting. Return ``True`` if it existed."""
    deleted, _ = UserSetting.objects.filter(
        user=user, module=module, key=key,
    ).delete()
    cache.delete(_cache_key(user.pk, module, key))
    return deleted > 0


def get_module_settings(user, module: str) -> dict[str, Any]:
    """Return all settings for a given module as a ``{key: value}`` dict."""
    return {
        s.key: s.value
        for s in UserSetting.objects.filter(user=user, module=module)
    }


def get_all_settings(user) -> list[dict]:
    """Return every setting for *user* as a list of dicts."""
    return [
        {'module': s.module, 'key': s.key, 'value': s.value}
        for s in UserSetting.objects.filter(user=user)
    ]

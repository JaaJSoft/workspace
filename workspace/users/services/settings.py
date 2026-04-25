"""Convenience helpers for reading / writing user settings from any module.

Usage from another app::

    from workspace.users.services.settings import get_setting, set_setting

    theme = get_setting(request.user, 'core', 'theme', default='light')
    set_setting(request.user, 'core', 'theme', 'dark')
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from workspace.common.cache import cached, invalidate_tags
from workspace.users.models import UserSetting

UTC = ZoneInfo('UTC')

_CACHE_TTL = 300  # 5 minutes
_DB_MISS = '__SETTING_NOT_FOUND__'


def _key_tag(user_id: int, module: str, key: str) -> str:
    return f'usetting:{user_id}:{module}:{key}'


def _module_tag(user_id: int, module: str) -> str:
    return f'usetting_mod:{user_id}:{module}'


def get_user_timezone(user) -> ZoneInfo:
    """Return the user's configured timezone, or UTC if unset/invalid."""
    tz_name = get_setting(user, 'core', 'timezone')
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return UTC


@cached(
    key=lambda user, module, key: f'usetting:{user.pk}:{module}:{key}',
    ttl=_CACHE_TTL,
    tags=lambda user, module, key: [_key_tag(user.pk, module, key)],
)
def _get_setting_raw(user, module: str, key: str) -> Any:
    """Return the raw stored value, or ``_DB_MISS`` if the setting doesn't exist."""
    try:
        return UserSetting.objects.get(user=user, module=module, key=key).value
    except UserSetting.DoesNotExist:
        return _DB_MISS


def get_setting(user, module: str, key: str, *, default: Any = None) -> Any:
    """Return the value of a single setting, or *default* if it does not exist."""
    value = _get_setting_raw(user, module, key)
    return default if value == _DB_MISS else value


def set_setting(user, module: str, key: str, value: Any) -> UserSetting:
    """Create or update a setting and return the model instance."""
    obj, _ = UserSetting.objects.update_or_create(
        user=user, module=module, key=key,
        defaults={'value': value},
    )
    invalidate_tags(
        _key_tag(user.pk, module, key),
        _module_tag(user.pk, module),
    )
    return obj


def delete_setting(user, module: str, key: str) -> bool:
    """Delete a setting. Return ``True`` if it existed."""
    deleted, _ = UserSetting.objects.filter(
        user=user, module=module, key=key,
    ).delete()
    invalidate_tags(
        _key_tag(user.pk, module, key),
        _module_tag(user.pk, module),
    )
    return deleted > 0


@cached(
    key=lambda user, module: f'usetting_mod:{user.pk}:{module}',
    ttl=_CACHE_TTL,
    tags=lambda user, module: [_module_tag(user.pk, module)],
)
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

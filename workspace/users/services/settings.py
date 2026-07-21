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

UTC = ZoneInfo("UTC")

_CACHE_TTL = 300  # 5 minutes


def _module_tag(user_id: int, module: str) -> str:
    return f"usetting_mod:{user_id}:{module}"


def get_user_timezone(user) -> ZoneInfo:
    """Return the user's configured timezone, or UTC if unset/invalid."""
    tz_name = get_setting(user, "core", "timezone")
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError, KeyError:
        return UTC


def get_setting(user, module: str, key: str, *, default: Any = None) -> Any:
    """Return the value of a single setting, or *default* if it does not exist.

    Reads through :func:`get_module_settings` so every key in a module shares a
    single cache entry: the first read of any key warms the whole module, and
    subsequent reads of any other key - present or absent - are served from that
    cached dict without touching the database. A key stored with the value
    ``None`` is distinguished from an absent key (``dict.get`` only falls back
    to *default* when the key is missing), preserving the previous semantics.
    """
    return get_module_settings(user, module).get(key, default)


def set_setting(user, module: str, key: str, value: Any) -> UserSetting:
    """Create or update a setting and return the model instance.

    Skips the database write entirely when the stored value already matches
    *value*. Saves one write transaction per click in flows that fire the
    same value repeatedly (clicking the active theme, double-submitted
    forms, click-spam, ...), which on SQLite means one less acquisition of
    the writer-lock per redundant call.

    The pre-check goes through the cached ``get_module_settings`` helper so
    a cache hit costs zero DB hits in the value lookup. If the cache says
    we're already at the target value we re-fetch the model instance and
    **re-verify the value from the DB** before treating as a no-op: this
    closes the race where the cache holds a stale snapshot equal to our
    target value while the DB has been moved to something else by a
    concurrent writer (invalidate-after-commit can be lost or delayed
    across processes). Without the second check we would silently drop
    the write and leave the DB at the concurrent writer's value. With it,
    we fall through to ``update_or_create`` and honour the caller's
    intent.
    """
    module_settings = get_module_settings(user, module)
    if key in module_settings and module_settings[key] == value:
        try:
            existing = UserSetting.objects.get(user=user, module=module, key=key)
        except UserSetting.DoesNotExist:
            pass  # row deleted out-of-band; fall through to create
        else:
            if existing.value == value:
                return existing  # confirmed no-op against fresh DB read
            # else: stale cache, DB drifted; fall through to write

    obj, _ = UserSetting.objects.update_or_create(
        user=user,
        module=module,
        key=key,
        defaults={"value": value},
    )
    invalidate_tags(_module_tag(user.pk, module))
    return obj


def delete_setting(user, module: str, key: str) -> bool:
    """Delete a setting. Return ``True`` if it existed."""
    deleted, _ = UserSetting.objects.filter(
        user=user,
        module=module,
        key=key,
    ).delete()
    invalidate_tags(_module_tag(user.pk, module))
    return deleted > 0


@cached(
    key=lambda user, module: f"usetting_mod:{user.pk}:{module}",
    ttl=_CACHE_TTL,
    tags=lambda user, module: [_module_tag(user.pk, module)],
)
def get_module_settings(user, module: str) -> dict[str, Any]:
    """Return all settings for a given module as a ``{key: value}`` dict."""
    return {
        s.key: s.value for s in UserSetting.objects.filter(user=user, module=module)
    }


def get_all_settings(user) -> list[dict]:
    """Return every setting for *user* as a list of dicts."""
    return [
        {"module": s.module, "key": s.key, "value": s.value}
        for s in UserSetting.objects.filter(user=user)
    ]

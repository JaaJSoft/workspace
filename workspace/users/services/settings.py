"""Convenience helpers for reading / writing user settings from any module.

Usage from another app::

    from workspace.users.services.settings import get_setting, set_setting

    theme = get_setting(request.user, 'core', 'theme', default='light')
    set_setting(request.user, 'core', 'theme', 'dark')
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings

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
    except ZoneInfoNotFoundError, KeyError, ValueError:
        return UTC


def get_user_language(user) -> str:
    """Return the user's interface language, or LANGUAGE_CODE if unset/unsupported.

    A stored code the deployment no longer offers (a language dropped from
    LANGUAGES) falls back like an unset one rather than reaching gettext.
    """
    code = get_setting(user, "core", "language")
    if code in dict(settings.LANGUAGES):
        return code
    return settings.LANGUAGE_CODE


def get_user_languages(user_ids) -> dict[int, str]:
    """Interface language of each of *user_ids*, in one query.

    For fan-outs (a notification to every member of a group), where calling
    :func:`get_user_language` per recipient would cost one query each on a
    cold cache. Reads the rows directly: a stale cache entry could only lag a
    write by its TTL, and the rows are what the cache is built from.
    """
    offered = dict(settings.LANGUAGES)
    stored = dict(
        UserSetting.objects.filter(
            user_id__in=user_ids, module="core", key="language"
        ).values_list("user_id", "value")
    )
    return {
        user_id: stored[user_id]
        if stored.get(user_id) in offered
        else settings.LANGUAGE_CODE
        for user_id in user_ids
    }


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

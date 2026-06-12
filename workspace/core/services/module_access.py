"""Per-module access control: which modules a user may access.

A module is *restrictable* when its ``AppConfig`` sets ``restrictable = True``.
Rules live in ``ModuleAccessRule`` (global or per-group). Resolution order for a
(user, module): superuser -> any group grant -> all group rules deny -> global
rule -> default open.
"""

from __future__ import annotations

from collections import defaultdict

from django.apps import apps

from workspace.common.cache import cached, invalidate_tags

_CACHE_TTL = 300
_CACHE_TAG = "module_access"


def restrictable_module_slugs() -> set[str]:
    """Return the slugs of modules whose AppConfig opts into restriction."""
    return {
        config.label
        for config in apps.get_app_configs()
        if getattr(config, "restrictable", False)
    }


def _decide(slug_rules, user_group_ids) -> bool:
    """Resolve one module's access from its rules for one user's groups."""
    group_rules = [r for r in slug_rules if r.group_id in user_group_ids]
    if group_rules:
        return any(r.is_enabled for r in group_rules)
    global_rule = next((r for r in slug_rules if r.group_id is None), None)
    if global_rule is not None:
        return global_rule.is_enabled
    return True


@cached(
    key=lambda user: f"module_access:enabled:{user.pk}",
    ttl=_CACHE_TTL,
    tags=lambda user: [_CACHE_TAG],
)
def enabled_module_slugs(user) -> set[str]:
    """Return the restrictable module slugs *user* is allowed to access."""
    restrictable = restrictable_module_slugs()
    if user.is_superuser:
        return set(restrictable)

    # Imported here to avoid a circular import (the model imports this module).
    from workspace.core.models import ModuleAccessRule

    rules = ModuleAccessRule.objects.filter(module_slug__in=restrictable)
    by_slug = defaultdict(list)
    for rule in rules:
        by_slug[rule.module_slug].append(rule)

    user_group_ids = set(user.groups.values_list("id", flat=True))
    return {
        slug for slug in restrictable if _decide(by_slug.get(slug, []), user_group_ids)
    }


def can_access_module(user, slug) -> bool:
    """True if *user* may access the module identified by *slug*."""
    if not slug or slug not in restrictable_module_slugs():
        return True
    return slug in enabled_module_slugs(user)


def invalidate_module_access_cache() -> None:
    """Drop every cached enabled-set (call from rule write paths)."""
    invalidate_tags(_CACHE_TAG)

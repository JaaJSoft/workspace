"""Per-module access control: which modules a user may access.

A module is *restrictable* when its ``AppConfig`` sets ``restrictable = True``.
Rules live in ``ModuleAccessRule`` (global or per-group). Resolution order for a
(user, module): superuser -> any group grant -> all group rules deny -> global
rule -> default open.
"""

from __future__ import annotations

from django.apps import apps


def restrictable_module_slugs() -> set[str]:
    """Return the slugs of modules whose AppConfig opts into restriction."""
    return {
        config.label
        for config in apps.get_app_configs()
        if getattr(config, "restrictable", False)
    }

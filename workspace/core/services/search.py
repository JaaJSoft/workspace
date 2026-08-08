"""Unified search over the module registry.

`registry.search` fans out to every registered provider and knows nothing
about per-user module visibility, so a raw call can surface hits from modules
the user is not allowed to see. Everything user-facing (the search API, the
assistant's `search_everything` tool) must go through the helpers here.
"""

from dataclasses import asdict

from ..module_registry import registry
from .module_visibility import filter_visible_commands, is_module_slug_visible

MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 10
MAX_LIMIT = 50


def search_modules(query, user, limit=DEFAULT_LIMIT) -> list[dict]:
    """Provider hits for *query*, restricted to modules *user* can see.

    *limit* is per provider, so the returned list holds up to
    ``limit * <number of registered providers>`` entries.
    """
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []
    return [
        hit
        for hit in registry.search(query, user, limit)
        if is_module_slug_visible(user, hit["module_slug"])
    ]


def search_commands(query, user) -> list[dict]:
    """Command palette entries matching *query*, filtered the same way."""
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []
    commands = filter_visible_commands(user, registry.search_commands(query))
    return [asdict(cmd) for cmd in commands]

"""Unified search over the module registry.

`registry.search` fans out to every registered provider and knows nothing
about per-user module visibility, so a raw call can surface hits from modules
the user is not allowed to see. Everything user-facing (the search API, the
assistant's `search_everything` tool) must go through the helpers here.
"""

from collections import defaultdict
from dataclasses import asdict

from ..module_registry import registry
from .module_visibility import filter_visible_commands, is_module_slug_visible

MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 10
MAX_LIMIT = 50


def _drop_refined(hits: list[dict]) -> list[dict]:
    """Keep a single row per entity when several providers answer with it.

    A provider naming another in its ``refines`` supersedes it for the uuids
    they both return: a markdown note is a File row, so `files` and `notes`
    both find it, and the notes row is the useful one (its url opens the
    editor rather than the file viewer).

    Only declared pairs are ever collapsed, so two providers colliding on a
    uuid without saying they overlap keep both rows.
    """
    refinements = registry.refinements()
    if not refinements:
        return hits

    providers_by_uuid = defaultdict(set)
    for hit in hits:
        providers_by_uuid[hit["uuid"]].add(hit["provider_slug"])

    def is_superseded(hit):
        rivals = providers_by_uuid[hit["uuid"]] - {hit["provider_slug"]}
        return any(hit["provider_slug"] in refinements.get(r, ()) for r in rivals)

    return [hit for hit in hits if not is_superseded(hit)]


def search_modules(query, user, limit=DEFAULT_LIMIT) -> list[dict]:
    """Provider hits for *query*, restricted to modules *user* can see.

    *limit* is per provider, so the returned list holds up to
    ``limit * <number of registered providers>`` entries - fewer once
    duplicates of the same entity are collapsed.

    Deduplication runs after the visibility filter on purpose: a user who
    cannot see the notes module must still get the files row for a note,
    which collapsing first would have dropped in favour of a notes row that
    is then filtered out.
    """
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []
    visible = [
        hit
        for hit in registry.search(query, user, limit)
        if is_module_slug_visible(user, hit["module_slug"])
    ]
    return _drop_refined(visible)


def search_commands(query, user) -> list[dict]:
    """Command palette entries matching *query*, filtered the same way."""
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []
    commands = filter_visible_commands(user, registry.search_commands(query))
    return [asdict(cmd) for cmd in commands]

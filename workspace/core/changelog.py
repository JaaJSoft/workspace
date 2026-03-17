"""Parse CHANGELOG.md into structured version entries with cached HTML."""

import re
from pathlib import Path

import mistune
from django.conf import settings
from django.core.cache import cache

_CHANGELOG_PATH = Path(settings.BASE_DIR) / 'CHANGELOG.md'
_CACHE_KEY_PREFIX = 'changelog'


def _make_cache_key():
    return f'{_CACHE_KEY_PREFIX}:{settings.APP_VERSION}'


def _parse_changelog():
    """Parse CHANGELOG.md into a list of ``{version, html}`` dicts."""
    try:
        raw = _CHANGELOG_PATH.read_text(encoding='utf-8')
    except FileNotFoundError:
        return []

    entries = []
    # Split on version headers: ## 0.11.0
    parts = re.split(r'^## +(.+)$', raw, flags=re.MULTILINE)
    # parts = ['preamble', 'version1', 'body1', 'version2', 'body2', ...]
    md = mistune.create_markdown()
    for i in range(1, len(parts), 2):
        version = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ''
        html = md(body.strip())
        entries.append({'version': version, 'html': html})

    return entries


def get_changelog_entries():
    """Return parsed changelog entries, cached by app version."""
    key = _make_cache_key()
    entries = cache.get(key)
    if entries is None:
        entries = _parse_changelog()
        cache.set(key, entries, timeout=None)
    return entries

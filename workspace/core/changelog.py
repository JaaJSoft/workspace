"""Parse CHANGELOG.md into structured version entries with cached HTML."""

import re
from pathlib import Path

import mistune
from django.conf import settings

from workspace.common.cache import cached

_CHANGELOG_PATH = Path(settings.BASE_DIR) / "CHANGELOG.md"
_TITLE_SEPARATORS = (" \u2014 ", " \u2013 ", " - ", ": ")


def parse_version(version):
    """Convert a version string like '0.20.0' to a comparable tuple ``(0, 20, 0)``.

    Returns an empty tuple for non-numeric values (e.g. ``'dev'``) so callers can
    detect them and skip ordered comparisons.
    """
    if not version or not isinstance(version, str):
        return ()
    parts = []
    for chunk in version.split("."):
        if not chunk.isdigit():
            return ()
        parts.append(int(chunk))
    return tuple(parts)


def _split_version_and_title(heading):
    """Split '0.18.0 — Performance' into ('0.18.0', 'Performance').

    Supports em-dash (—), en-dash (–), hyphen (-), and colon as separators.
    Returns an empty title when none is present.
    """
    for sep in _TITLE_SEPARATORS:
        if sep in heading:
            version, _, title = heading.partition(sep)
            return version.strip(), title.strip()
    return heading.strip(), ""


def _parse_changelog():
    """Parse CHANGELOG.md into a list of ``{version, title, html}`` dicts."""
    try:
        raw = _CHANGELOG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    entries = []
    parts = re.split(r"^## +(.+)$", raw, flags=re.MULTILINE)
    md = mistune.create_markdown()
    for i in range(1, len(parts), 2):
        version, title = _split_version_and_title(parts[i])
        body = parts[i + 1] if i + 1 < len(parts) else ""
        html = md(body.strip())
        entries.append({"version": version, "title": title, "html": html})

    return entries


@cached(key=lambda: f"changelog:{settings.APP_VERSION}", ttl=None)
def get_changelog_entries():
    """Return parsed changelog entries, cached for the lifetime of the deploy.

    The key embeds ``APP_VERSION`` so a redeploy bumps the effective key
    without any explicit invalidation step.
    """
    return _parse_changelog()


def get_latest_version():
    """Return the topmost (newest) version listed in CHANGELOG.md, or None.

    Releases are written newest-first by convention, so the first entry is
    the latest. Acts as the source of truth for "is there something new to
    read" comparisons, decoupled from the runtime ``APP_VERSION`` env var.
    """
    entries = get_changelog_entries()
    return entries[0]["version"] if entries else None

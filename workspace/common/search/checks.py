"""Startup checks for the full-text search backends.

The FTS5 probe in this package answers "can we search at all"; this answers
"can we search file contents", which needs a newer SQLite than FTS5 itself.
Both are silent capability gaps rather than crashes, so they are worth
surfacing at check time instead of at the first failed query.
"""

import sqlite3

from django.core.checks import Warning
from django.db import connections

# contentless_delete=1, which a DerivedFulltextIndex needs to drop a row from
# a contentless FTS5 table, landed in SQLite 3.43 (2023-08).
CONTENTLESS_DELETE_MIN_VERSION = (3, 43)


def check_sqlite_fts_support(app_configs, **kwargs):
    """Warn when the SQLite build cannot carry a contentless FTS5 index."""
    if not any(conn.vendor == "sqlite" for conn in connections.all()):
        return []
    if sqlite3.sqlite_version_info[:2] >= CONTENTLESS_DELETE_MIN_VERSION:
        return []
    have = sqlite3.sqlite_version
    want = ".".join(str(part) for part in CONTENTLESS_DELETE_MIN_VERSION)
    return [
        Warning(
            f"SQLite {have} is too old for contentless FTS5 deletes (needs {want}+).",
            hint=(
                "The migration that creates the file content search index "
                "will fail on this build. Upgrade SQLite, or run on "
                "PostgreSQL."
            ),
            id="common.W001",
        )
    ]

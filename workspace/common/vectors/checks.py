"""Startup check: say so when vector search can only run on the fallback.

The fallback is correct, only slower, so this is a warning and only issued
once something registers a vector index.
"""

from django.core.checks import Warning
from django.db import connections

from .postgres import pgvector_available
from .schema import registered_vector_indexes
from .sqlite import sqlite_vec_loadable


def check_vector_backends(app_configs, databases=None, **kwargs):
    """Warn when a database has no vector extension to index registered vectors.

    SQLite is answered without touching the database (the extension is loaded
    into a scratch connection). PostgreSQL has to be asked, so only when the
    check framework hands over that database - `migrate` does, a bare `check`
    does not.
    """
    if not registered_vector_indexes():
        return []
    warnings = []
    for conn in connections.all():
        if conn.vendor == "sqlite" and not sqlite_vec_loadable():
            warnings.append(
                _fallback_warning(
                    conn.alias,
                    "sqlite-vec cannot be loaded by this Python.",
                    "Install the sqlite-vec wheel for this platform, on a Python "
                    "built with SQLite extension loading.",
                )
            )
        elif (
            conn.vendor == "postgresql"
            and conn.alias in (databases or ())
            and not pgvector_available(conn)
        ):
            warnings.append(
                _fallback_warning(
                    conn.alias,
                    "the pgvector extension is not available on this server.",
                    "Use an image that ships it (pgvector/pgvector), then run "
                    "`manage.py rebuild_vector_index`.",
                )
            )
    return warnings


def _fallback_warning(alias, reason, hint):
    return Warning(
        f"Database '{alias}': {reason} Vector search scans every row of a "
        f"partition instead of using an index.",
        hint=hint,
        id="common.W002",
    )

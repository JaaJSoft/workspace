"""Startup check: say so when vector search is served by the fallback.

The fallback is correct, only slower, so this is a warning and only issued
once something registers a vector index.
"""

from django.core.checks import Warning
from django.db import connections

from . import active_backend, sqlite_vec_available
from .postgres import pgvector_available, pgvector_version
from .schema import registered_vector_indexes
from .sqlite import sqlite_vec_loadable

_REBUILD = "run `manage.py rebuild_vector_index`"


def check_vector_backends(app_configs, databases=None, **kwargs):
    """Warn for each registered index the fallback serves, and say why.

    A database the check framework hands over (`migrate` does, `check
    --database` does) is asked which backend actually serves each index:
    the extension may load and the index still be missing. Any other SQLite
    database is answered without touching it - can sqlite-vec load at all.
    """
    indexes = registered_vector_indexes()
    if not indexes:
        return []
    warnings = []
    for conn in connections.all():
        if conn.alias in (databases or ()):
            warnings += _served_by_fallback(conn, indexes)
        elif conn.vendor == "sqlite" and not sqlite_vec_loadable():
            warnings.append(
                _warning(
                    f"Database '{conn.alias}': sqlite-vec cannot be loaded by this "
                    f"Python.",
                    "Install the sqlite-vec wheel for this platform, on a Python "
                    f"built with SQLite extension loading, then {_REBUILD}.",
                )
            )
    return warnings


def _served_by_fallback(conn, indexes):
    tables = set(conn.introspection.table_names())
    warnings = []
    for index in indexes:
        # migrate runs the checks before the migrations: an index whose table
        # is not there yet is about to be created along with it.
        if index.table not in tables:
            continue
        if active_backend(index, conn).name != "numpy":
            continue
        warnings.append(
            _warning(
                f"Database '{conn.alias}': {index.table}.{index.source_column} is "
                f"searched without an index ({_cause(conn)}).",
                _hint(conn),
            )
        )
    return warnings


def _cause(conn):
    if conn.vendor == "sqlite":
        if not sqlite_vec_available(conn):
            return "sqlite-vec is not loaded"
        return "the vec0 table is missing"
    if conn.vendor == "postgresql":
        if not pgvector_available(conn):
            return "pgvector is not available on this server"
        if pgvector_version(conn) is None:
            return "pgvector is not enabled in this database"
        return "the vector column is missing"
    return f"no vector extension for {conn.vendor}"


def _hint(conn):
    if conn.vendor == "sqlite" and not sqlite_vec_available(conn):
        return (
            "Install the sqlite-vec wheel for this platform, on a Python built "
            f"with SQLite extension loading, then {_REBUILD}."
        )
    if conn.vendor == "postgresql" and not pgvector_available(conn):
        return f"Use an image that ships it (pgvector/pgvector), then {_REBUILD}."
    if conn.vendor == "postgresql" and pgvector_version(conn) is None:
        return (
            "pgvector is not a trusted extension: run the rebuild once as a "
            f"superuser ({_REBUILD} connected as one)."
        )
    if conn.vendor in ("sqlite", "postgresql"):
        return f"The extension is there; {_REBUILD}."
    return "Only SQLite and PostgreSQL have an indexed backend."


def _warning(msg, hint):
    return Warning(
        f"{msg} Vector search scans every row of a partition instead.",
        hint=hint,
        id="common.W002",
    )

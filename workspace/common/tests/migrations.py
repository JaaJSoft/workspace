"""Test helper for calling a data migration's callable directly."""

from types import SimpleNamespace

from django.db import DEFAULT_DB_ALIAS, connections


def schema_editor_stub(using=DEFAULT_DB_ALIAS):
    """Stand-in for the schema editor a ``RunPython`` callable receives.

    A data migration reads ``schema_editor.connection.alias`` to route its
    queries at the database being migrated, rather than at whichever one
    happens to be the default. A test that calls such a function directly has
    to supply that much: passing ``None`` was only ever safe while the function
    ignored the argument entirely.

    Only ``.connection.alias`` is needed, so this is a namespace rather than a
    real schema editor - building one opens a connection and starts a
    transaction the caller never asked for.
    """
    return SimpleNamespace(connection=connections[using])

"""Migration operation creating the derived structure of a VectorIndex.

Carries the literal SQL printed by ``manage.py vector_sql`` - a migration
never imports the live declaration - and decides at migrate time which of it
applies:

- PostgreSQL: always runs; the SQL itself checks for pgvector and leaves the
  table alone without it.
- SQLite: runs only where sqlite-vec is loaded, since a vec0 table cannot be
  created, nor dropped, without the module. Without it the fallback serves
  queries from the source column, and ``rebuild_vector_index`` creates the
  table once the extension is available.

Either way the migration applies, so the schema history is the same on every
installation.
"""

from django.db import router
from django.db.migrations.operations.base import Operation

from .indexing import backfill_pg_vectors
from .sqlite import sqlite_vec_loaded


class RunVectorIndexSQL(Operation):
    """Everything ``manage.py vector_sql`` prints, applied where it can be.

    On PostgreSQL the forward readies the column, fills whatever vectors are
    missing from their source (``pg_backfill``: the literal names and size,
    as printed), then builds the index over the filled column - so a replay
    on a populated table, a rollback then re-apply included, leaves the index
    whole.
    """

    reduces_to_sql = False

    def __init__(
        self,
        *,
        pg_forward,
        pg_backfill,
        pg_index,
        pg_reverse,
        sqlite_forward,
        sqlite_reverse,
        hints=None,
    ):
        self.pg_forward = pg_forward
        self.pg_backfill = pg_backfill
        self.pg_index = pg_index
        self.pg_reverse = pg_reverse
        self.sqlite_forward = sqlite_forward
        self.sqlite_reverse = sqlite_reverse
        self.hints = hints or {}

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if not router.allow_migrate(
            schema_editor.connection.alias, app_label, **self.hints
        ):
            return
        conn = schema_editor.connection
        if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
            self._run_script(schema_editor, self.pg_forward)
            backfill_pg_vectors(conn, **self.pg_backfill)
            self._run_script(schema_editor, self.pg_index)
        elif conn.vendor == "sqlite" and sqlite_vec_loaded(conn):
            self._run_script(schema_editor, self.sqlite_forward)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if not router.allow_migrate(
            schema_editor.connection.alias, app_label, **self.hints
        ):
            return
        conn = schema_editor.connection
        if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
            self._run_script(schema_editor, self.pg_reverse)
        elif conn.vendor == "sqlite" and sqlite_vec_loaded(conn):
            self._run_script(schema_editor, self.sqlite_reverse)

    def describe(self):
        return "Create a vector index (pgvector or sqlite-vec, when available)"

    @staticmethod
    def _run_script(schema_editor, sql):
        # prepare_sql_script splits on SQLite and keeps the script whole on
        # PostgreSQL, which a DO $$ ... $$ block needs.
        for statement in schema_editor.connection.ops.prepare_sql_script(sql):
            schema_editor.execute(statement, params=None)

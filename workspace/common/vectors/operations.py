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

from django.db.migrations.operations.base import Operation

from .sqlite import sqlite_vec_loaded


class RunVectorIndexSQL(Operation):
    reduces_to_sql = False
    reversible = True

    def __init__(self, *, pg_forward, pg_reverse, sqlite_forward, sqlite_reverse):
        self.pg_forward = pg_forward
        self.pg_reverse = pg_reverse
        self.sqlite_forward = sqlite_forward
        self.sqlite_reverse = sqlite_reverse

    def deconstruct(self):
        return (
            self.__class__.__qualname__,
            [],
            {
                "pg_forward": self.pg_forward,
                "pg_reverse": self.pg_reverse,
                "sqlite_forward": self.sqlite_forward,
                "sqlite_reverse": self.sqlite_reverse,
            },
        )

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._run(schema_editor, pg=self.pg_forward, sqlite=self.sqlite_forward)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        self._run(schema_editor, pg=self.pg_reverse, sqlite=self.sqlite_reverse)

    def describe(self):
        return "Create a vector index (pgvector or sqlite-vec, when available)"

    @staticmethod
    def _run(schema_editor, *, pg, sqlite):
        conn = schema_editor.connection
        if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
            schema_editor.execute(pg, params=None)
        elif conn.vendor == "sqlite" and sqlite_vec_loaded(conn):
            for statement in conn.ops.prepare_sql_script(sqlite):
                schema_editor.execute(statement, params=None)

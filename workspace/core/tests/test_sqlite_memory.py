"""What a SQLite connection costs a web worker in memory.

Under gevent workers every incoming HTTP connection runs in its own greenlet
and gets its own database connection, so a cost per connection is multiplied
by the requests in flight. Two ways that took a worker past its limit:

- A connection kept for reuse is never reused: it only waits, parsed schema
  and page cache included, for the garbage collector to break its reference
  cycle. A burst of thumbnail requests grew a worker by hundreds of MB.
- The private page cache is on the heap. Reads only stay off it while the
  mmap covers them; past it, or from a WAL a long reader keeps from being
  checkpointed, each connection fills up to its cache_size.

The test database is in memory, where Django ignores ``close()`` and none of
the PRAGMAs on the file apply, so the checks run against a SQLite file
configured like the default database.
"""

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.utils import ConnectionHandler


@contextmanager
def file_connection():
    """A connection to a fresh SQLite file, set up as the default database."""
    with tempfile.TemporaryDirectory() as tmp:
        config = {
            **settings.DATABASES["default"],
            "NAME": str(Path(tmp) / "db.sqlite3"),
        }
        config.pop("TEST", None)
        db = ConnectionHandler({"default": config})["default"]
        try:
            db.ensure_connection()
            yield db
        finally:
            db.close()


class SQLiteMemoryTests(unittest.TestCase):
    def setUp(self):
        if connection.vendor != "sqlite":
            self.skipTest("Only applies to SQLite backends.")

    def pragma(self, db, name):
        with db.cursor() as cursor:
            return cursor.execute(f"PRAGMA {name}").fetchone()[0]

    def test_request_end_closes_the_connection(self):
        with file_connection() as db:
            # What request_finished runs, through close_old_connections().
            db.close_if_unusable_or_obsolete()

            self.assertIsNone(db.connection)

    def test_the_mmap_covers_a_database_of_real_size(self):
        with file_connection() as db:
            # SQLite clamps silently: read back what it actually maps.
            self.assertGreaterEqual(self.pragma(db, "mmap_size"), 2**31 - 2**16)

    def test_the_private_page_cache_stays_small(self):
        with file_connection() as db:
            self.assertEqual(self.pragma(db, "cache_size"), -8000)

    def test_a_checkpoint_truncates_the_wal(self):
        with file_connection() as db:
            self.assertEqual(self.pragma(db, "journal_mode"), "wal")
            self.assertEqual(self.pragma(db, "journal_size_limit"), 64 * 2**20)

    def test_a_sort_spills_to_a_file(self):
        with file_connection() as db:
            self.assertEqual(self.pragma(db, "temp_store"), 1)  # FILE

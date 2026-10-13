"""A SQLite connection must not outlive the request that opened it.

Under gevent workers every incoming HTTP connection runs in its own greenlet
and gets its own database connection. A connection kept for reuse is never
reused: it only waits, page cache and mmap included, for the garbage
collector to break its reference cycle. A burst of thumbnail requests grew a
worker by hundreds of MB that way.

The test database is in memory, where Django ignores ``close()``, so the
check runs against a SQLite file configured like the default database.
"""

import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.utils import ConnectionHandler


class SQLiteConnectionLifetimeTests(unittest.TestCase):
    def test_request_end_closes_the_connection(self):
        if connection.vendor != "sqlite":
            self.skipTest("Only SQLite keeps one connection per greenlet.")
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                **settings.DATABASES["default"],
                "NAME": str(Path(tmp) / "db.sqlite3"),
            }
            config.pop("TEST", None)
            db = ConnectionHandler({"default": config})["default"]
            try:
                db.ensure_connection()

                # What request_finished runs, through close_old_connections().
                db.close_if_unusable_or_obsolete()

                self.assertIsNone(db.connection)
            finally:
                db.close()

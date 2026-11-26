"""SQLite file databases configured like the default one.

The test database is in memory, where Django ignores ``close()`` and the
PRAGMAs about the file (WAL, mmap, auto_vacuum on a file that shrinks) do not
apply, so tests about them open a file of their own.
"""

import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.db.utils import ConnectionHandler


@contextmanager
def file_connection(path=None):
    """A connection to a SQLite file, set up as the default database.

    A fresh file in a temporary directory unless ``path`` names one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        config = {
            **settings.DATABASES["default"],
            "NAME": str(path or Path(tmp) / "db.sqlite3"),
        }
        config.pop("TEST", None)
        db = ConnectionHandler({"default": config})["default"]
        try:
            db.ensure_connection()
            yield db
        finally:
            db.close()

"""Database configuration and backend-specific tuning."""

from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import BASE_DIR, TESTING
from .env import env_bool, env_non_negative_int

# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
# Use DATABASE_URL for any backend:
#   sqlite:///db.sqlite3               (relative to BASE_DIR)
#   sqlite:////absolute/path/db.sqlite3
#   postgres://user:pass@host:5432/dbname
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60
    ),
}


def _pool_options():
    """Arguments of the psycopg ConnectionPool, from the DB_POOL_* env vars.

    The pool belongs to the process: every greenlet of a gevent web worker, or
    every thread of a Celery child, borrows from the same one, so max_size is
    how many queries the process can run at once. Above it a request waits up
    to timeout seconds for a connection, then fails.
    """
    min_size = env_non_negative_int("DB_POOL_MIN_SIZE")
    if min_size is None:
        min_size = 4
    max_size = env_non_negative_int("DB_POOL_MAX_SIZE")
    if max_size is None:
        max_size = max(min_size, 1)
    timeout = env_non_negative_int("DB_POOL_TIMEOUT")
    max_idle = env_non_negative_int("DB_POOL_MAX_IDLE")
    max_lifetime = env_non_negative_int("DB_POOL_MAX_LIFETIME")
    # psycopg only checks these when the first request opens the pool: a
    # mistake would start every process fine and fail every request.
    if max_size < max(min_size, 1):
        raise ImproperlyConfigured(
            f"DB_POOL_MAX_SIZE ({max_size}) must be at least 1 and at least "
            f"DB_POOL_MIN_SIZE ({min_size})"
        )
    if timeout == 0:
        raise ImproperlyConfigured(
            "DB_POOL_TIMEOUT must be at least 1 second: at 0 a request fails "
            "as soon as every connection is busy"
        )
    return {
        "min_size": min_size,
        "max_size": max_size,
        "timeout": 30 if timeout is None else timeout,
        "max_idle": 600 if max_idle is None else max_idle,
        "max_lifetime": 3600 if max_lifetime is None else max_lifetime,
    }


# PostgreSQL: connection pooling + Prometheus DB metrics
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["ENGINE"] = "django_prometheus.db.backends.postgresql"
    # Django 6.0 rejects pool=True combined with CONN_MAX_AGE>0
    # ("Pooling doesn't support persistent connections"). The pool itself
    # already keeps connections alive, so persistent-connection caching is
    # both redundant and incompatible.
    DATABASES["default"]["CONN_MAX_AGE"] = 0
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = False
    DATABASES["default"]["OPTIONS"] = {
        **DATABASES["default"].get("OPTIONS", {}),
        "pool": _pool_options(),
    }

# SQLite-specific optimizations (WAL mode, PRAGMAs)
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    try:
        Path(DATABASES["default"]["NAME"]).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best effort: if the directory cannot be created (read-only fs,
        # exotic path), sqlite will raise a clearer error at connect time.
        pass

    # Closed at the end of every request. Gunicorn runs gevent workers, where
    # Django's per-thread connections become per-greenlet: each incoming HTTP
    # connection opens its own, which nothing can reuse once that greenlet
    # ends. Kept open, it lingers in a reference cycle until a full garbage
    # collection, holding its parsed schema and page cache - a grid of
    # thumbnails leaves hundreds behind. Opening a SQLite file costs well
    # under a ms.
    DATABASES["default"]["CONN_MAX_AGE"] = 0
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"]["timeout"] = 60.0
    # Force BEGIN IMMEDIATE for every transaction. Django's default
    # (BEGIN DEFERRED) starts each transaction as a reader and tries to
    # upgrade to a writer on the first INSERT/UPDATE/DELETE. When two
    # concurrent connections both hold a read snapshot and both try to
    # upgrade, SQLite raises SQLITE_BUSY_SNAPSHOT immediately - the
    # busy_timeout PRAGMA below does NOT apply to snapshot upgrades.
    # IMMEDIATE acquires the writer-lock at BEGIN time, so busy_timeout
    # works as intended and concurrent writers serialize cleanly instead
    # of failing with "database is locked".
    DATABASES["default"]["OPTIONS"]["transaction_mode"] = "IMMEDIATE"
    # Memory: every connection has a private page cache, on the heap, while
    # the mmap is the OS page cache, shared by every connection and process
    # and reclaimable. Reads of the database file go through the mmap when
    # it covers them, so the private cache only holds what it cannot: pages
    # still in the WAL, pages being written, and the database past the mmap.
    # A connection lives one request (CONN_MAX_AGE above), so a large private
    # cache buys nothing beyond it and costs that much per concurrent
    # request - 64 MB each took a worker past its limit while the WAL was
    # held back.
    # - mmap_size: the most SQLite accepts (SQLITE_MAX_MMAP_SIZE, 2 GiB), so
    #   that a database of real size is read through the mmap, not the heap.
    #   Only address space is reserved.
    # - cache_size: 8 MB per connection.
    # - journal_size_limit: a checkpoint truncates the WAL back to 64 MB,
    #   rather than leaving it at its largest until the nightly
    #   wal_checkpoint(TRUNCATE) of core.db_maintenance. A long read
    #   transaction stops checkpoints from catching up, and the WAL grows.
    # - temp_store=FILE: a sort or temporary table spills past the page cache
    #   into a temporary file instead of holding all its rows in memory.
    DATABASES["default"]["OPTIONS"]["init_command"] = (
        "PRAGMA journal_mode=WAL; "
        "PRAGMA foreign_keys=ON; "
        "PRAGMA busy_timeout=60000; "
        "PRAGMA synchronous=NORMAL; "
        "PRAGMA temp_store=FILE; "
        "PRAGMA mmap_size=2147418112; "
        "PRAGMA cache_size=-8000; "
        "PRAGMA journal_size_limit=67108864;"
    )

# E2E runs drive the app through a real server process, which needs a test
# database on disk rather than the default in-memory one.
if (
    TESTING
    and DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    and env_bool("E2E")
):
    DATABASES["default"].setdefault("TEST", {})
    DATABASES["default"]["TEST"]["NAME"] = str(BASE_DIR / "test_db.sqlite3")

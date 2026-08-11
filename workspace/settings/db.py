"""Database configuration and backend-specific tuning."""

from pathlib import Path

import dj_database_url

from .base import BASE_DIR, TESTING
from .env import env_bool

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
        "pool": True,
    }

# SQLite-specific optimizations (WAL mode, PRAGMAs)
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    try:
        Path(DATABASES["default"]["NAME"]).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best effort: if the directory cannot be created (read-only fs,
        # exotic path), sqlite will raise a clearer error at connect time.
        pass

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
    DATABASES["default"]["OPTIONS"]["init_command"] = (
        "PRAGMA journal_mode=WAL; "
        "PRAGMA foreign_keys=ON; "
        "PRAGMA busy_timeout=60000; "
        "PRAGMA synchronous=NORMAL; "
        "PRAGMA temp_store=MEMORY; "
        "PRAGMA mmap_size=268435456; "
        "PRAGMA cache_size=-64000;"
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

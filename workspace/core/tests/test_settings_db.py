"""PostgreSQL connection pool settings, as they are evaluated from the environment."""

import importlib
import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from psycopg_pool import ConnectionPool

from workspace.settings import db as db_settings

POSTGRES_URL = "postgres://user:password@localhost:5432/workspace"
POOL_VARS = (
    "DB_POOL_MIN_SIZE",
    "DB_POOL_MAX_SIZE",
    "DB_POOL_TIMEOUT",
    "DB_POOL_MAX_IDLE",
    "DB_POOL_MAX_LIFETIME",
)


class PoolOptionsTests(SimpleTestCase):
    def setUp(self):
        self.addCleanup(importlib.reload, db_settings)

    def load(self, **env):
        environ = {
            key: value for key, value in os.environ.items() if key not in POOL_VARS
        }
        environ.update(DATABASE_URL=POSTGRES_URL, **env)
        with patch.dict(os.environ, environ, clear=True):
            settings = importlib.reload(db_settings)
        return settings.DATABASES["default"]["OPTIONS"]["pool"]

    def test_defaults_are_psycopg_defaults(self):
        self.assertEqual(
            self.load(),
            {
                "min_size": 4,
                "max_size": 4,
                "timeout": 30,
                "max_idle": 600,
                "max_lifetime": 3600,
            },
        )

    def test_every_option_is_read_from_the_environment(self):
        self.assertEqual(
            self.load(
                DB_POOL_MIN_SIZE="2",
                DB_POOL_MAX_SIZE="20",
                DB_POOL_TIMEOUT="10",
                DB_POOL_MAX_IDLE="120",
                DB_POOL_MAX_LIFETIME="1800",
            ),
            {
                "min_size": 2,
                "max_size": 20,
                "timeout": 10,
                "max_idle": 120,
                "max_lifetime": 1800,
            },
        )

    def test_max_size_follows_min_size_when_unset(self):
        self.assertEqual(self.load(DB_POOL_MIN_SIZE="8")["max_size"], 8)

    def test_an_empty_minimum_still_allows_one_connection(self):
        """psycopg refuses a pool whose sizes are both 0."""
        self.assertEqual(self.load(DB_POOL_MIN_SIZE="0")["max_size"], 1)

    def test_psycopg_accepts_the_options(self):
        """Django hands OPTIONS["pool"] to ConnectionPool as keyword arguments,
        and only when the first query opens the pool."""
        options = self.load(DB_POOL_MIN_SIZE="1", DB_POOL_MAX_SIZE="5")
        pool = ConnectionPool(conninfo="", open=False, **options)
        self.assertEqual((pool.min_size, pool.max_size), (1, 5))
        self.assertEqual(pool.timeout, 30)

    def test_invalid_values_fail_when_the_settings_load(self):
        cases = {
            "maximum below the minimum": {
                "DB_POOL_MIN_SIZE": "8",
                "DB_POOL_MAX_SIZE": "4",
            },
            "no connection at all": {"DB_POOL_MIN_SIZE": "0", "DB_POOL_MAX_SIZE": "0"},
            "zero timeout": {"DB_POOL_TIMEOUT": "0"},
            "zero idle time": {"DB_POOL_MAX_IDLE": "0"},
            "zero lifetime": {"DB_POOL_MAX_LIFETIME": "0"},
            "negative size": {"DB_POOL_MAX_SIZE": "-1"},
            "negative lifetime": {"DB_POOL_MAX_LIFETIME": "-1"},
            "not a number": {"DB_POOL_TIMEOUT": "30s"},
        }
        for label, env in cases.items():
            with self.subTest(label), self.assertRaises(ImproperlyConfigured):
                self.load(**env)

    def test_sqlite_has_no_pool(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///db.sqlite3"}):
            settings = importlib.reload(db_settings)
        self.assertNotIn("pool", settings.DATABASES["default"].get("OPTIONS", {}))

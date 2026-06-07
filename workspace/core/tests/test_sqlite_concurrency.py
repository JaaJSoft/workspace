"""Regression test for SQLite transaction-mode configuration.

Background
----------
Django's default SQLite transaction mode is ``DEFERRED``: each transaction
starts as a reader and tries to upgrade to a writer at the first INSERT /
UPDATE / DELETE. When two connections both hold a read snapshot and both
try to upgrade, SQLite raises ``SQLITE_BUSY_SNAPSHOT`` *immediately* -
the ``busy_timeout`` PRAGMA does NOT apply to snapshot upgrades, so the
60-second wait we configured in ``settings.py`` is bypassed and the user
sees ``OperationalError: database is locked``.

The fix is ``OPTIONS['transaction_mode'] = 'IMMEDIATE'`` which makes every
transaction acquire the writer-lock at ``BEGIN`` time. ``busy_timeout``
then applies normally and concurrent writers serialize cleanly.

This test pins the configuration so a future refactor cannot silently
remove the option.

Note on concurrent reproduction
-------------------------------
A true end-to-end reproduction would spawn several OS processes hitting a
shared file-based SQLite. Django's default test runner uses an in-memory
DB with ``cache=shared`` which exercises a different SQLite locking
regime (table-level ``SQLITE_LOCKED`` in shared cache, not the cross-
process ``SQLITE_BUSY_SNAPSHOT`` we hit in production). A multi-process
integration test would belong with the E2E suite (``E2E=1`` env), not
in a unit test.
"""

from __future__ import annotations

import unittest

from django.conf import settings
from django.db import connection


class SQLiteTransactionModeConfigTests(unittest.TestCase):
    def test_transaction_mode_is_immediate_when_sqlite(self):
        if connection.vendor != "sqlite":
            self.skipTest("Configuration only applies to SQLite backends.")
        options = settings.DATABASES["default"].get("OPTIONS", {})
        self.assertEqual(
            options.get("transaction_mode"),
            "IMMEDIATE",
            "SQLite must use BEGIN IMMEDIATE to avoid SQLITE_BUSY_SNAPSHOT "
            "on concurrent update_or_create calls; see the comment block in "
            "workspace/settings.py next to this option.",
        )

"""Tests for workspace.core.tasks.

The Celery task exercises SQLite-specific pragmas (VACUUM, WAL checkpoint,
integrity_check) that can't safely run inside Django's TestCase transaction.
We therefore patch ``workspace.core.tasks.connection`` to drive the control
flow without actually touching the database.
"""

from contextlib import contextmanager
from unittest import mock

from django.test import SimpleTestCase

from workspace.core import tasks as core_tasks
from workspace.core.tasks import _run_maintenance, db_maintenance


class _FakeCursor:
    """Minimal context-manager cursor that records executed SQL."""

    def __init__(self, responses=None):
        self.executed = []
        # responses: mapping sql-prefix -> row(s) tuple
        self.responses = responses or {}
        self._last_sql = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql):
        self.executed.append(sql)
        self._last_sql = sql

    def fetchone(self):
        for prefix, rows in self.responses.items():
            if self._last_sql and self._last_sql.startswith(prefix):
                return rows[0] if isinstance(rows, list) else rows
        return None

    def fetchall(self):
        for prefix, rows in self.responses.items():
            if self._last_sql and self._last_sql.startswith(prefix):
                return rows if isinstance(rows, list) else [rows]
        return []


class _FakeConnection:
    def __init__(self, vendor='sqlite', cursor=None):
        self.vendor = vendor
        self.settings_dict = {'NAME': '/tmp/fake.sqlite3'}
        self._cursor = cursor or _FakeCursor()

    def cursor(self):
        return self._cursor


@contextmanager
def _patched_connection(vendor='sqlite', cursor=None):
    fake = _FakeConnection(vendor=vendor, cursor=cursor)
    with mock.patch.object(core_tasks, 'connection', fake):
        yield fake


class IsSqliteTests(SimpleTestCase):
    def test_returns_true_for_sqlite_vendor(self):
        with _patched_connection(vendor='sqlite'):
            self.assertTrue(core_tasks._is_sqlite())

    def test_returns_false_for_postgres_vendor(self):
        with _patched_connection(vendor='postgresql'):
            self.assertFalse(core_tasks._is_sqlite())


class RunMaintenanceSkipsNonSqliteTests(SimpleTestCase):
    def test_returns_skipped_dict_when_not_sqlite(self):
        with _patched_connection(vendor='postgresql'):
            result = _run_maintenance()

        self.assertTrue(result['skipped'])
        self.assertIn('not SQLite', result['reason'])


class RunMaintenanceFullFlowTests(SimpleTestCase):
    def setUp(self):
        self.cursor = _FakeCursor(responses={
            'PRAGMA wal_checkpoint': (0, 10, 10),
            'PRAGMA integrity_check': [('ok',)],
        })

    def test_runs_all_maintenance_steps(self):
        sizes = iter([2048, 1024])
        with _patched_connection(cursor=self.cursor), \
                mock.patch('workspace.core.tasks.os.path.getsize', side_effect=lambda _: next(sizes)):
            result = _run_maintenance()

        executed = [sql for sql in self.cursor.executed]
        self.assertTrue(any(s.startswith('PRAGMA optimize') for s in executed))
        self.assertTrue(any(s.startswith('PRAGMA wal_checkpoint') for s in executed))
        self.assertTrue(any(s.startswith('VACUUM') for s in executed))
        self.assertTrue(any(s.startswith('PRAGMA integrity_check') for s in executed))

        # Structured result
        self.assertIn('optimize_ms', result)
        self.assertEqual(result['wal_checkpoint'], {
            'return_code': 0,
            'pages_written': 10,
            'pages_checkpointed': 10,
        })
        self.assertEqual(result['size_before'], 2048)
        self.assertEqual(result['size_after'], 1024)
        self.assertEqual(result['size_saved'], 1024)
        self.assertEqual(result['integrity_check'], 'ok')

    def test_skip_vacuum_flag(self):
        with _patched_connection(cursor=self.cursor):
            result = _run_maintenance(skip_vacuum=True)

        self.assertTrue(result['vacuum_skipped'])
        self.assertNotIn('vacuum_ms', result)
        self.assertFalse(any(s.startswith('VACUUM') for s in self.cursor.executed))

    def test_skip_integrity_check_flag(self):
        with _patched_connection(cursor=self.cursor), \
                mock.patch('workspace.core.tasks.os.path.getsize', return_value=0):
            result = _run_maintenance(skip_integrity_check=True)

        self.assertTrue(result['integrity_check_skipped'])
        self.assertNotIn('integrity_check', result)

    def test_integrity_check_failure_is_recorded(self):
        self.cursor.responses['PRAGMA integrity_check'] = [('row 42 malformed',)]
        with _patched_connection(cursor=self.cursor), \
                mock.patch('workspace.core.tasks.os.path.getsize', return_value=0):
            result = _run_maintenance()

        self.assertEqual(result['integrity_check'], 'row 42 malformed')

    def test_getsize_errors_are_handled(self):
        with _patched_connection(cursor=self.cursor), \
                mock.patch('workspace.core.tasks.os.path.getsize', side_effect=OSError):
            result = _run_maintenance()

        self.assertIsNone(result['size_before'])
        self.assertIsNone(result['size_after'])
        self.assertNotIn('size_saved', result)


class DbMaintenanceTaskTests(SimpleTestCase):
    def test_skipped_when_not_sqlite(self):
        with _patched_connection(vendor='postgresql'):
            result = db_maintenance.run()

        self.assertEqual(result, {'skipped': True, 'reason': 'not SQLite'})

    def test_happy_path_returns_total_ms(self):
        cursor = _FakeCursor(responses={
            'PRAGMA wal_checkpoint': (0, 0, 0),
            'PRAGMA integrity_check': [('ok',)],
        })
        with _patched_connection(cursor=cursor), \
                mock.patch('workspace.core.tasks.os.path.getsize', return_value=0):
            result = db_maintenance.run()

        self.assertIn('total_ms', result)
        self.assertEqual(result['integrity_check'], 'ok')

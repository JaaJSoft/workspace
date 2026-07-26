"""Tests for the SQLite maintenance task and its cadence flags.

These pin *which* pragmas run for a given flag combination, so the split
between the cheap daily pass (optimize + WAL checkpoint) and the heavy weekly
pass (VACUUM + integrity_check) cannot silently regress.
"""

from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from workspace.core.tasks import _run_maintenance, db_maintenance

_IS_SQLITE = connection.vendor == "sqlite"


def _capture_sql(callback):
    """Run ``callback`` while recording every SQL statement executed."""
    executed = []

    def wrapper(execute, sql, params, many, context):
        executed.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        result = callback()
    return executed, result


def _ran_vacuum(statements):
    return any(s.strip().upper().startswith("VACUUM") for s in statements)


def _ran_integrity_check(statements):
    return any("integrity_check" in s for s in statements)


def _ran_optimize(statements):
    return any("PRAGMA optimize" in s for s in statements)


def _ran_wal_checkpoint(statements):
    return any("wal_checkpoint" in s for s in statements)


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class LightMaintenanceTests(TransactionTestCase):
    """The daily pass keeps the cheap operations and skips the heavy ones.

    Uses TransactionTestCase so the WAL checkpoint runs in autocommit rather
    than against an open transaction (which locks the database)."""

    def test_light_run_runs_optimize_and_checkpoint_only(self):
        statements, result = _capture_sql(
            lambda: _run_maintenance(skip_vacuum=True, skip_integrity_check=True)
        )

        self.assertTrue(_ran_optimize(statements))
        self.assertTrue(_ran_wal_checkpoint(statements))
        self.assertFalse(_ran_vacuum(statements))
        self.assertFalse(_ran_integrity_check(statements))

        self.assertTrue(result["vacuum_skipped"])
        self.assertTrue(result["integrity_check_skipped"])
        self.assertNotIn("vacuum_ms", result)
        self.assertNotIn("integrity_check", result)

    def test_task_forwards_skip_flags(self):
        statements, _ = _capture_sql(
            lambda: db_maintenance.run(skip_vacuum=True, skip_integrity_check=True)
        )

        self.assertFalse(_ran_vacuum(statements))
        self.assertFalse(_ran_integrity_check(statements))


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class FullMaintenanceTests(TransactionTestCase):
    """The weekly pass runs everything; VACUUM needs autocommit, hence the
    TransactionTestCase base (VACUUM cannot run inside an open transaction)."""

    def test_full_run_runs_vacuum_and_integrity_check(self):
        statements, result = _capture_sql(_run_maintenance)

        self.assertTrue(_ran_optimize(statements))
        self.assertTrue(_ran_wal_checkpoint(statements))
        self.assertTrue(_ran_vacuum(statements))
        self.assertTrue(_ran_integrity_check(statements))

        self.assertNotIn("vacuum_skipped", result)
        self.assertNotIn("integrity_check_skipped", result)
        self.assertIn("vacuum_ms", result)
        self.assertEqual(result["integrity_check"], "ok")


class BeatScheduleTests(SimpleTestCase):
    """Guard the availability fix: the daily pass must skip the heavy ops, and
    the two passes must never fire on the same day."""

    def test_daily_pass_skips_heavy_ops(self):
        entry = settings.CELERY_BEAT_SCHEDULE["db-maintenance"]
        self.assertEqual(entry["task"], "core.db_maintenance")
        self.assertTrue(entry["kwargs"]["skip_vacuum"])
        self.assertTrue(entry["kwargs"]["skip_integrity_check"])

    def test_full_pass_runs_everything(self):
        entry = settings.CELERY_BEAT_SCHEDULE["db-maintenance-full"]
        self.assertEqual(entry["task"], "core.db_maintenance")
        # No kwargs -> _run_maintenance defaults -> VACUUM + integrity_check run.
        self.assertNotIn("kwargs", entry)

    def test_passes_never_collide(self):
        daily = settings.CELERY_BEAT_SCHEDULE["db-maintenance"]["schedule"]
        full = settings.CELERY_BEAT_SCHEDULE["db-maintenance-full"]["schedule"]
        self.assertEqual(daily.hour, full.hour)
        self.assertEqual(daily.minute, full.minute)
        self.assertEqual(daily.day_of_week & full.day_of_week, set())

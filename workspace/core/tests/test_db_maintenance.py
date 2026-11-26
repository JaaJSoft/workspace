"""Tests for the SQLite maintenance pass, the full VACUUM and snapshots.

They pin how long each operation can hold the write lock: the scheduled pass
only ever frees a bounded number of pages per statement, the full VACUUM that
switches an existing database runs after migrate, never on a schedule, and
refuses to start without the disk it needs, and a snapshot is written while
another connection holds the write lock.
"""

import os
import re
import sqlite3
import tempfile
from collections import namedtuple
from pathlib import Path
from unittest import TestCase, mock, skipUnless

from django.conf import settings
from django.core.management import CommandError, call_command
from django.core.management.sql import emit_post_migrate_signal
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from workspace.core.services import sqlite_maintenance
from workspace.core.services.sqlite_maintenance import (
    NotEnoughSpace,
    ensure_free_space,
    incremental_vacuum,
    run_maintenance,
    snapshot,
    switch_to_incremental,
)
from workspace.core.tasks import db_maintenance
from workspace.core.tests.sqlite_files import file_connection

_IS_SQLITE = connection.vendor == "sqlite"

DiskUsage = namedtuple("DiskUsage", "total used free")


def _capture_sql(db, callback):
    """Run ``callback`` while recording every SQL statement ``db`` executes."""
    executed = []

    def wrapper(execute, sql, params, many, context):
        executed.append(sql)
        return execute(sql, params, many, context)

    with db.execute_wrapper(wrapper):
        result = callback()
    return executed, result


def _ran(statements, pattern):
    return any(re.search(pattern, s, re.IGNORECASE) for s in statements)


def _full_vacuums(statements):
    return [s for s in statements if s.strip().upper().rstrip(";") == "VACUUM"]


def _pragma(db, name):
    with db.cursor() as cursor:
        cursor.execute(f"PRAGMA {name};")
        return cursor.fetchone()[0]


def _fill_and_free(db, rows=400):
    """Write rows then delete most of them, leaving free pages in the file."""
    with db.cursor() as cursor:
        cursor.execute("CREATE TABLE blob (data BLOB);")
        cursor.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n "
            "WHERE i < %s) INSERT INTO blob SELECT randomblob(8000) FROM n;",
            [rows],
        )
        cursor.execute("DELETE FROM blob WHERE rowid > 10;")
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")


def _legacy_database(path):
    """A database created before auto_vacuum was set, with free pages."""
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("PRAGMA journal_mode=WAL;")
    raw.execute("CREATE TABLE blob (data BLOB);")
    raw.executemany("INSERT INTO blob VALUES (randomblob(8000));", [()] * 400)
    raw.execute("DELETE FROM blob WHERE rowid > 10;")
    raw.close()


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class IncrementalModeTests(TestCase):
    def test_a_new_database_is_in_incremental_mode(self):
        with file_connection() as db:
            with db.cursor() as cursor:
                cursor.execute("CREATE TABLE t (a);")
            self.assertEqual(_pragma(db, "auto_vacuum"), 2)
            self.assertEqual(_pragma(db, "journal_mode"), "wal")

    def test_an_existing_database_keeps_its_mode_until_it_is_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            _legacy_database(path)
            with file_connection(path) as db:
                self.assertEqual(_pragma(db, "auto_vacuum"), 0)


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class IncrementalVacuumTests(TestCase):
    def test_free_pages_go_back_to_the_filesystem(self):
        with file_connection() as db:
            _fill_and_free(db)
            free_before = _pragma(db, "freelist_count")
            size_before = os.path.getsize(db.settings_dict["NAME"])

            result = run_maintenance(db)

            self.assertEqual(result["auto_vacuum"], "incremental")
            self.assertEqual(result["pages_freed"], free_before)
            self.assertEqual(result["freelist_count"], 0)
            self.assertLess(result["size_after"], size_before // 2)
            self.assertEqual(
                os.path.getsize(db.settings_dict["NAME"]), result["size_after"]
            )

    def test_every_statement_frees_a_bounded_number_of_pages(self):
        # A bare "PRAGMA incremental_vacuum" frees every page at once, in one
        # write transaction as long as the file is large.
        with file_connection() as db:
            _fill_and_free(db)
            free = _pragma(db, "freelist_count")
            statements, result = _capture_sql(
                db,
                lambda: incremental_vacuum(db, step_pages=50, pause_seconds=0),
            )

        vacuums = [s for s in statements if "incremental_vacuum" in s]
        self.assertTrue(vacuums)
        self.assertTrue(all("incremental_vacuum(50)" in s for s in vacuums))
        self.assertEqual(result["steps"], -(-free // 50))
        self.assertFalse(_full_vacuums(statements))

    def test_the_budget_stops_the_run(self):
        with file_connection() as db:
            _fill_and_free(db)
            free = _pragma(db, "freelist_count")

            result = incremental_vacuum(db, step_pages=50, budget_seconds=0)

        self.assertEqual(result["steps"], 1)
        self.assertEqual(result["pages_freed"], 50)
        self.assertEqual(result["freelist_count"], free - 50)

    def test_refuses_to_run_inside_a_transaction(self):
        with file_connection() as db:
            _fill_and_free(db)
            with mock.patch.object(db, "in_atomic_block", True):
                with self.assertRaises(RuntimeError):
                    incremental_vacuum(db)
            self.assertGreater(_pragma(db, "freelist_count"), 0)

    def test_a_database_in_none_mode_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            _legacy_database(path)
            with file_connection(path) as db:
                free = _pragma(db, "freelist_count")
                statements, result = _capture_sql(db, lambda: run_maintenance(db))

        self.assertEqual(result["auto_vacuum"], "none")
        self.assertEqual(result["steps"], 0)
        self.assertEqual(result["freelist_count"], free)
        self.assertFalse(_ran(statements, r"incremental_vacuum"))
        self.assertFalse(_full_vacuums(statements))


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class SwitchToIncrementalTests(TestCase):
    def test_rewrites_an_existing_database_into_incremental_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            _legacy_database(path)
            size_before = os.path.getsize(path)
            with file_connection(path) as db:
                result = switch_to_incremental(db)

                self.assertEqual(result["auto_vacuum_before"], "none")
                self.assertEqual(result["auto_vacuum"], "incremental")
                self.assertEqual(_pragma(db, "auto_vacuum"), 2)
                self.assertEqual(_pragma(db, "freelist_count"), 0)
            self.assertLess(os.path.getsize(path), size_before // 2)

    def test_leaves_an_incremental_database_alone(self):
        with file_connection() as db:
            _fill_and_free(db)
            statements, result = _capture_sql(db, lambda: switch_to_incremental(db))
        self.assertIsNone(result)
        self.assertFalse(_full_vacuums(statements))

    def test_refuses_to_start_without_enough_free_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            _legacy_database(path)
            with file_connection(path) as db:
                with mock.patch.object(
                    sqlite_maintenance, "disk_usage", return_value=DiskUsage(0, 0, 0)
                ):
                    statements, _ = _capture_sql(
                        db,
                        lambda: self.assertRaises(
                            NotEnoughSpace, switch_to_incremental, db
                        ),
                    )
                self.assertEqual(_pragma(db, "auto_vacuum"), 0)
        self.assertFalse(_full_vacuums(statements))


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class SwitchAfterMigrateTests(TransactionTestCase):
    def test_migrate_switches_the_database(self):
        # What migrate sends once it is done; only apps with models send it.
        with mock.patch.object(
            sqlite_maintenance, "switch_to_incremental", return_value=None
        ) as switch:
            emit_post_migrate_signal(verbosity=0, interactive=False, db="default")

        self.assertTrue(switch.called)
        self.assertTrue(all(c.args == (connection,) for c in switch.call_args_list))

    def test_a_short_disk_leaves_the_switch_to_the_next_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            _legacy_database(path)
            with file_connection(path) as db:
                with (
                    mock.patch.object(sqlite_maintenance, "connections", {"x": db}),
                    mock.patch.object(
                        sqlite_maintenance,
                        "disk_usage",
                        return_value=DiskUsage(0, 0, 0),
                    ),
                    self.assertLogs(sqlite_maintenance.logger, "WARNING"),
                ):
                    sqlite_maintenance.switch_after_migrate(sender=None, using="x")
                self.assertEqual(_pragma(db, "auto_vacuum"), 0)


class FreeSpaceTests(SimpleTestCase):
    def test_directories_on_one_disk_add_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            with mock.patch.object(
                sqlite_maintenance, "disk_usage", return_value=DiskUsage(0, 0, 100)
            ):
                ensure_free_space({a: 60})
                with self.assertRaises(NotEnoughSpace):
                    ensure_free_space({a: 60, b: 60})


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class SnapshotTests(TestCase):
    def test_writes_a_copy_while_a_writer_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "snapshot.sqlite3"
            with file_connection() as db:
                with db.cursor() as cursor:
                    cursor.execute("CREATE TABLE t (a);")
                    cursor.execute("INSERT INTO t VALUES (1);")
                writer = sqlite3.connect(db.settings_dict["NAME"], isolation_level=None)
                writer.execute("BEGIN IMMEDIATE;")
                writer.execute("INSERT INTO t VALUES (2);")
                try:
                    result = snapshot(target, db)
                finally:
                    writer.execute("COMMIT;")
                    writer.close()

            copy = sqlite3.connect(target)
            rows = copy.execute("SELECT a FROM t;").fetchall()
            copy.close()
        self.assertEqual(rows, [(1,)])
        self.assertEqual(result["path"], str(target))

    def test_refuses_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "snapshot.sqlite3"
            target.write_bytes(b"keep me")
            with file_connection() as db, self.assertRaises(FileExistsError):
                snapshot(target, db)
            self.assertEqual(target.read_bytes(), b"keep me")

    def test_refuses_without_enough_free_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "snapshot.sqlite3"
            with file_connection() as db:
                with db.cursor() as cursor:
                    cursor.execute("CREATE TABLE t (a);")
                with (
                    mock.patch.object(
                        sqlite_maintenance,
                        "disk_usage",
                        return_value=DiskUsage(0, 0, 0),
                    ),
                    self.assertRaises(NotEnoughSpace),
                ):
                    snapshot(target, db)
            self.assertFalse(target.exists())


@skipUnless(_IS_SQLITE, "maintenance only runs on SQLite")
class MaintenanceTaskTests(TransactionTestCase):
    """The task on the default database; autocommit, hence TransactionTestCase."""

    def test_daily_pass_runs_no_full_vacuum_and_no_integrity_check(self):
        statements, result = _capture_sql(connection, lambda: db_maintenance.run())

        self.assertTrue(_ran(statements, r"PRAGMA optimize"))
        self.assertTrue(_ran(statements, r"wal_checkpoint"))
        self.assertFalse(_full_vacuums(statements))
        self.assertFalse(_ran(statements, r"integrity_check"))
        self.assertNotIn("integrity_check", result)

    def test_weekly_pass_adds_the_integrity_check(self):
        statements, result = _capture_sql(
            connection, lambda: db_maintenance.run(integrity_check=True)
        )

        self.assertTrue(_ran(statements, r"integrity_check"))
        self.assertFalse(_full_vacuums(statements))
        self.assertEqual(result["integrity_check"], "ok")


class SnapshotCommandTests(SimpleTestCase):
    def test_refuses_an_existing_file(self):
        with tempfile.NamedTemporaryFile() as existing:
            with self.assertRaises(CommandError):
                call_command("db_snapshot", existing.name)


class BeatScheduleTests(SimpleTestCase):
    def test_no_entry_runs_a_full_vacuum(self):
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            if entry["task"] == "core.db_maintenance":
                self.assertEqual(
                    set(entry.get("kwargs", {})) - {"integrity_check"}, set(), name
                )

    def test_integrity_check_runs_weekly(self):
        daily = settings.CELERY_BEAT_SCHEDULE["db-maintenance"]
        weekly = settings.CELERY_BEAT_SCHEDULE["db-maintenance-weekly"]
        self.assertNotIn("kwargs", daily)
        self.assertEqual(weekly["kwargs"], {"integrity_check": True})

    def test_passes_never_collide(self):
        daily = settings.CELERY_BEAT_SCHEDULE["db-maintenance"]["schedule"]
        weekly = settings.CELERY_BEAT_SCHEDULE["db-maintenance-weekly"]["schedule"]
        self.assertEqual(daily.hour, weekly.hour)
        self.assertEqual(daily.minute, weekly.minute)
        self.assertEqual(daily.day_of_week & weekly.day_of_week, set())
        self.assertEqual(daily.day_of_week | weekly.day_of_week, set(range(7)))

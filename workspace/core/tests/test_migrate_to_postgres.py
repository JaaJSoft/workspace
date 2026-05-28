import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from workspace.core.management.commands.migrate_to_postgres import (
    EXCLUDED_APPS,
    TARGET_ALIAS,
    Command,
)

MODULE = "workspace.core.management.commands.migrate_to_postgres"


class PreCheckTests(TestCase):
    """Pre-check validation (source must be SQLite, target must be PostgreSQL)."""

    def test_rejects_non_sqlite_source(self):
        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.postgresql"},
        ))
        with patch(f"{MODULE}.connections", mock_conns):
            with self.assertRaises(CommandError) as ctx:
                call_command("migrate_to_postgres", "postgres://u:p@host/db")
            self.assertIn("expected SQLite", str(ctx.exception))

    def test_rejects_non_postgres_target(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("migrate_to_postgres", "sqlite:///other.db")
        self.assertIn("only migrates TO PostgreSQL", str(ctx.exception))

    def test_rejects_unreachable_target(self):
        from django.db import connections as real_conns

        with self.assertRaises(CommandError) as ctx:
            call_command(
                "migrate_to_postgres",
                "postgres://u:p@unreachable-host:5432/db",
            )
        self.assertIn("Cannot connect", str(ctx.exception))

        # Clean up the target alias registered during the failed attempt
        real_conns.databases.pop(TARGET_ALIAS, None)


class SafeUrlTests(TestCase):
    """Password masking in displayed URLs."""

    def test_masks_password(self):
        cmd = Command()
        result = cmd._safe_url("postgres://admin:secret@host:5432/db")
        self.assertNotIn("secret", result)
        self.assertIn("admin", result)
        self.assertIn("****", result)

    def test_no_password(self):
        cmd = Command()
        url = "postgres://host:5432/db"
        self.assertEqual(cmd._safe_url(url), url)


class CountDumpTests(TestCase):
    """Record counting in JSON dump files."""

    def test_counts_records(self):
        data = [{"model": "auth.user", "pk": 1}, {"model": "auth.user", "pk": 2}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            path = Path(f.name)

        try:
            cmd = Command()
            self.assertEqual(cmd._count_records_in_dump(path), 2)
        finally:
            path.unlink(missing_ok=True)

    def test_empty_dump(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump([], f)
            f.flush()
            path = Path(f.name)

        try:
            cmd = Command()
            self.assertEqual(cmd._count_records_in_dump(path), 0)
        finally:
            path.unlink(missing_ok=True)


def _empty_dump(name, *args, **kwargs):
    """Fake call_command side effect that writes an empty JSON list when
    dumpdata is invoked (the command now passes ``stdout=<file>`` instead
    of ``--output``).
    """
    if name == "dumpdata" and "stdout" in kwargs:
        kwargs["stdout"].write("[]")


class DryRunTests(TestCase):
    """Dry-run exports data but does not write to target."""

    @patch(f"{MODULE}.call_command")
    def test_dry_run_skips_import(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        mock_call.side_effect = _empty_dump

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_pending_migrations", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=True,
                keep_dump=False,
            )

        call_names = [c[0][0] for c in mock_call.call_args_list]
        self.assertIn("dumpdata", call_names)
        self.assertNotIn("loaddata", call_names)
        # --dry-run must leave the target untouched: no schema or seed-data
        # migrations may run on it. Regression test: an earlier version
        # called migrate before the dry-run early-return, which created
        # the full schema and inserted seed rows (default mail labels,
        # the assistant bot user) on the target.
        self.assertNotIn("migrate", call_names)


class FullMigrationTests(TestCase):
    """Full migration flow with mocked commands."""

    @patch(f"{MODULE}.call_command")
    def test_full_run_calls_loaddata(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        mock_call.side_effect = _empty_dump

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_pending_migrations", return_value=[]),
            patch.object(cmd, "_truncate_target_tables", return_value=0),
            patch.object(cmd, "_reset_sequences"),
            patch.object(cmd, "_verify_counts", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=False,
                keep_dump=False,
            )

        call_names = [c[0][0] for c in mock_call.call_args_list]
        self.assertIn("migrate", call_names)
        self.assertIn("dumpdata", call_names)
        self.assertIn("loaddata", call_names)

    @patch(f"{MODULE}.call_command")
    def test_loaddata_uses_target_alias(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        mock_call.side_effect = _empty_dump

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_pending_migrations", return_value=[]),
            patch.object(cmd, "_truncate_target_tables", return_value=0),
            patch.object(cmd, "_reset_sequences"),
            patch.object(cmd, "_verify_counts", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=False,
                keep_dump=False,
            )

        loaddata_call = [
            c for c in mock_call.call_args_list if c[0][0] == "loaddata"
        ][0]
        self.assertEqual(loaddata_call[1]["database"], TARGET_ALIAS)

    @patch(f"{MODULE}.call_command")
    def test_keep_dump_preserves_file(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        # Need to capture the dump file path - dumpdata writes via ``stdout``
        # which is opened on the file in `handle()`. Use a side effect that
        # records the underlying file path via the stream's ``.name``.
        dump_path_holder = {}

        def fake_call_command(name, *args, **kwargs):
            if name == "dumpdata" and "stdout" in kwargs:
                dump_path_holder["path"] = Path(kwargs["stdout"].name)
                kwargs["stdout"].write("[]")

        mock_call.side_effect = fake_call_command

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_pending_migrations", return_value=[]),
            patch.object(cmd, "_truncate_target_tables", return_value=0),
            patch.object(cmd, "_reset_sequences"),
            patch.object(cmd, "_verify_counts", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=False,
                keep_dump=True,
            )

        dump_path = dump_path_holder.get("path")
        try:
            self.assertIsNotNone(dump_path)
            self.assertTrue(dump_path.exists())
        finally:
            if dump_path:
                dump_path.unlink(missing_ok=True)


class ExcludedAppsTests(TestCase):
    """Verify the exclusion list covers contenttypes and permissions."""

    def test_excludes_contenttypes(self):
        self.assertIn("contenttypes", EXCLUDED_APPS)

    def test_excludes_auth_permission(self):
        self.assertIn("auth.permission", EXCLUDED_APPS)


class SourceMigrationsPrecheckTests(TestCase):
    """Pre-check that aborts if the source SQLite has unapplied migrations."""

    @patch(f"{MODULE}.call_command")
    def test_aborts_when_source_has_pending_migrations(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(
                cmd,
                "_pending_migrations",
                return_value=[("myapp", "0042_something"), ("other", "0001_init")],
            ),
        ):
            with self.assertRaises(CommandError) as ctx:
                cmd.handle(
                    database_url="postgres://u:p@host/db",
                    dry_run=True,
                    keep_dump=False,
                )

        self.assertIn("unapplied migrations", str(ctx.exception))
        self.assertIn("myapp.0042_something", str(ctx.exception))
        # dumpdata should never have been reached
        call_names = [c[0][0] for c in mock_call.call_args_list]
        self.assertNotIn("dumpdata", call_names)

    @patch(f"{MODULE}.call_command")
    def test_continues_when_source_is_up_to_date(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        def fake_call_command(name, *args, **kwargs):
            # dumpdata uses stdout= kwarg now, write an empty fixture there
            if name == "dumpdata" and "stdout" in kwargs:
                kwargs["stdout"].write("[]")

        mock_call.side_effect = fake_call_command

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_pending_migrations", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=True,
                keep_dump=False,
            )

        call_names = [c[0][0] for c in mock_call.call_args_list]
        self.assertIn("dumpdata", call_names)


class TruncateAndSequenceResetTests(TestCase):
    """The full-run path must wipe target tables before loaddata and reset sequences after."""

    @patch(f"{MODULE}.call_command")
    def test_truncate_runs_before_loaddata_and_sequence_reset_after(self, mock_call):
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()

        mock_conns = MagicMock()
        mock_conns.__getitem__ = MagicMock(return_value=MagicMock(
            settings_dict={"ENGINE": "django.db.backends.sqlite3"},
        ))
        mock_conns.databases = {}

        ordering = []

        def fake_call_command(name, *args, **kwargs):
            if name == "dumpdata" and "stdout" in kwargs:
                kwargs["stdout"].write("[]")
            if name == "loaddata":
                ordering.append("loaddata")

        mock_call.side_effect = fake_call_command

        def record_truncate():
            ordering.append("truncate")
            return 3

        def record_reset():
            ordering.append("reset")

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_pending_migrations", return_value=[]),
            patch.object(cmd, "_truncate_target_tables", side_effect=record_truncate),
            patch.object(cmd, "_reset_sequences", side_effect=record_reset),
            patch.object(cmd, "_verify_counts", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=False,
                keep_dump=False,
            )

        self.assertEqual(ordering, ["truncate", "loaddata", "reset"])


class VerifyCountsExclusionTests(TestCase):
    """Count verification must skip the apps excluded from the dump."""

    def test_skips_contenttype_and_permission(self):
        cmd = Command()

        regular = MagicMock()
        regular._meta.proxy = False
        regular._meta.swapped = False
        regular._meta.label_lower = "myapp.thing"
        regular.objects.using.return_value.count.side_effect = [10, 20]

        excluded_perm = MagicMock()
        excluded_perm._meta.proxy = False
        excluded_perm._meta.swapped = False
        excluded_perm._meta.label_lower = "auth.permission"
        excluded_perm.objects.using.return_value.count.side_effect = [100, 50]

        excluded_ct = MagicMock()
        excluded_ct._meta.proxy = False
        excluded_ct._meta.swapped = False
        excluded_ct._meta.label_lower = "contenttypes.contenttype"
        excluded_ct.objects.using.return_value.count.side_effect = [30, 20]

        with patch(
            f"{MODULE}.apps.get_models",
            return_value=[regular, excluded_perm, excluded_ct],
        ):
            mismatches = cmd._verify_counts()

        # Only the non-excluded model should produce a mismatch entry.
        self.assertEqual(mismatches, [("myapp.thing", 10, 20)])
        # Excluded models should never have been queried.
        excluded_perm.objects.using.assert_not_called()
        excluded_ct.objects.using.assert_not_called()

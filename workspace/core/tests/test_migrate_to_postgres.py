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

        def fake_call_command(name, *args, **kwargs):
            if name == "dumpdata":
                output_idx = list(args).index("--output")
                Path(args[output_idx + 1]).write_text("[]")

        mock_call.side_effect = fake_call_command

        with patch(f"{MODULE}.connections", mock_conns):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=True,
                keep_dump=False,
            )

        call_names = [c[0][0] for c in mock_call.call_args_list]
        self.assertIn("dumpdata", call_names)
        self.assertNotIn("loaddata", call_names)


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

        def fake_call_command(name, *args, **kwargs):
            if name == "dumpdata":
                output_idx = list(args).index("--output")
                Path(args[output_idx + 1]).write_text("[]")

        mock_call.side_effect = fake_call_command

        with (
            patch(f"{MODULE}.connections", mock_conns),
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

        def fake_call_command(name, *args, **kwargs):
            if name == "dumpdata":
                output_idx = list(args).index("--output")
                Path(args[output_idx + 1]).write_text("[]")

        mock_call.side_effect = fake_call_command

        with (
            patch(f"{MODULE}.connections", mock_conns),
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

        dump_path = None

        def fake_call_command(name, *args, **kwargs):
            nonlocal dump_path
            if name == "dumpdata":
                output_idx = list(args).index("--output")
                dump_path = Path(args[output_idx + 1])
                dump_path.write_text("[]")

        mock_call.side_effect = fake_call_command

        with (
            patch(f"{MODULE}.connections", mock_conns),
            patch.object(cmd, "_verify_counts", return_value=[]),
        ):
            cmd.handle(
                database_url="postgres://u:p@host/db",
                dry_run=False,
                keep_dump=True,
            )

        try:
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

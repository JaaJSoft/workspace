from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase


class FtsSqlCommandTests(SimpleTestCase):
    def test_prints_all_four_blocks(self):
        out = StringIO()
        call_command(
            "fts_sql",
            "workspace.common.tests.test_search_schema.MAIL_LIKE",
            stdout=out,
        )
        text = out.getvalue()
        for marker in (
            "-- PG_FORWARD",
            "-- PG_REVERSE",
            "-- SQLITE_FORWARD",
            "-- SQLITE_REVERSE",
        ):
            self.assertIn(marker, text)
        self.assertIn("CREATE VIRTUAL TABLE mail_mailmessage_fts", text)
        self.assertIn("USING gin (search_tsv)", text)

    def test_bad_path_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("fts_sql", "workspace.nowhere.MISSING")

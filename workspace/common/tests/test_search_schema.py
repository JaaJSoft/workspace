from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from workspace.common.search import fts5_available, schema
from workspace.common.search.schema import PG_TSV_COLUMN, Col, FulltextIndex

# Module-level so the fts_sql command test (and humans) can import them.
CHAT_LIKE = FulltextIndex(table="chat_message", columns=(Col("body", cap=100_000),))
MAIL_LIKE = FulltextIndex(
    table="mail_mailmessage",
    columns=(
        Col("subject", weight="A"),
        Col("snippet", weight="C"),
        Col("from_email", weight="B"),
        Col("from_name", weight="B"),
        Col("body_text", weight="D", cap=100_000),
    ),
)


class DeclarationTests(SimpleTestCase):
    def test_names_are_derived_from_the_table(self):
        self.assertEqual(CHAT_LIKE.fts_table, "chat_message_fts")
        self.assertEqual(CHAT_LIKE.gin_index, "chat_message_tsv_gin")
        self.assertEqual(PG_TSV_COLUMN, "search_tsv")

    def test_string_columns_are_normalized_to_col(self):
        idx = FulltextIndex(table="t", columns=("a", Col("b", weight="C")))
        self.assertEqual(idx.columns, (Col("a"), Col("b", weight="C")))
        self.assertEqual(idx.fallback_fields, ("a", "b"))

    def test_empty_columns_rejected(self):
        with self.assertRaises(ValueError):
            FulltextIndex(table="t", columns=())

    def test_bad_weight_rejected(self):
        with self.assertRaises(ValueError):
            Col("a", weight="Z")


class SqlGenerationTests(SimpleTestCase):
    def test_pg_forward_single_capped_column(self):
        self.assertEqual(
            CHAT_LIKE.pg_forward_sql(),
            (
                "DROP INDEX IF EXISTS chat_message_tsv_gin;\n"
                "ALTER TABLE chat_message DROP COLUMN IF EXISTS search_tsv;\n"
                "\n"
                "ALTER TABLE chat_message ADD COLUMN search_tsv tsvector\n"
                "  GENERATED ALWAYS AS (\n"
                "    setweight(to_tsvector('simple', f_unaccent("
                "left(coalesce(body, ''), 100000))), 'A')\n"
                "  ) STORED;\n"
                "\n"
                "CREATE INDEX chat_message_tsv_gin ON chat_message "
                "USING gin (search_tsv);"
            ),
        )

    def test_pg_forward_two_column_exact_string(self):
        idx = FulltextIndex(
            table="t",
            columns=(Col("a", weight="A"), Col("b", weight="B", cap=50)),
        )
        self.assertEqual(
            idx.pg_forward_sql(),
            (
                "DROP INDEX IF EXISTS t_tsv_gin;\n"
                "ALTER TABLE t DROP COLUMN IF EXISTS search_tsv;\n"
                "\n"
                "ALTER TABLE t ADD COLUMN search_tsv tsvector\n"
                "  GENERATED ALWAYS AS (\n"
                "    setweight(to_tsvector('simple', f_unaccent("
                "coalesce(a, ''))), 'A') ||\n"
                "    setweight(to_tsvector('simple', f_unaccent("
                "left(coalesce(b, ''), 50))), 'B')\n"
                "  ) STORED;\n"
                "\n"
                "CREATE INDEX t_tsv_gin ON t USING gin (search_tsv);"
            ),
        )

    def test_pg_reverse_drops_index_and_column(self):
        self.assertEqual(
            CHAT_LIKE.pg_reverse_sql(),
            (
                "DROP INDEX IF EXISTS chat_message_tsv_gin;\n"
                "ALTER TABLE chat_message DROP COLUMN IF EXISTS search_tsv;"
            ),
        )

    def test_pg_forward_weighted_multi_column(self):
        sql = MAIL_LIKE.pg_forward_sql()
        self.assertIn(
            "setweight(to_tsvector('simple', f_unaccent(coalesce(subject, ''))), 'A')",
            sql,
        )
        self.assertIn(
            "setweight(to_tsvector('simple', "
            "f_unaccent(coalesce(from_email, ''))), 'B')",
            sql,
        )
        self.assertIn(
            "setweight(to_tsvector('simple', "
            "f_unaccent(left(coalesce(body_text, ''), 100000))), 'D')",
            sql,
        )
        # One setweight per column, joined with tsvector concatenation.
        self.assertEqual(sql.count("setweight("), 5)

    def test_sqlite_forward_single_column(self):
        self.assertEqual(
            CHAT_LIKE.sqlite_forward_sql(),
            (
                "DROP TRIGGER IF EXISTS chat_message_fts_ai;\n"
                "DROP TRIGGER IF EXISTS chat_message_fts_ad;\n"
                "DROP TRIGGER IF EXISTS chat_message_fts_au;\n"
                "DROP TABLE IF EXISTS chat_message_fts;\n"
                "\n"
                "CREATE VIRTUAL TABLE chat_message_fts USING fts5(\n"
                "  body,\n"
                "  content='chat_message', content_rowid='rowid',\n"
                "  tokenize='unicode61 remove_diacritics 2'\n"
                ");\n"
                "\n"
                "CREATE TRIGGER chat_message_fts_ai AFTER INSERT "
                "ON chat_message BEGIN\n"
                "  INSERT INTO chat_message_fts(rowid, body)\n"
                "  VALUES (new.rowid, new.body);\n"
                "END;\n"
                "\n"
                "CREATE TRIGGER chat_message_fts_ad AFTER DELETE "
                "ON chat_message BEGIN\n"
                "  INSERT INTO chat_message_fts(chat_message_fts, rowid, body)\n"
                "  VALUES ('delete', old.rowid, old.body);\n"
                "END;\n"
                "\n"
                "CREATE TRIGGER chat_message_fts_au AFTER UPDATE "
                "ON chat_message BEGIN\n"
                "  INSERT INTO chat_message_fts(chat_message_fts, rowid, body)\n"
                "  VALUES ('delete', old.rowid, old.body);\n"
                "  INSERT INTO chat_message_fts(rowid, body)\n"
                "  VALUES (new.rowid, new.body);\n"
                "END;\n"
                "\n"
                "INSERT INTO chat_message_fts(chat_message_fts) "
                "VALUES ('rebuild');\n"
                "INSERT INTO chat_message_fts(chat_message_fts, rank)\n"
                "  VALUES ('rank', 'bm25(10.0)');"
            ),
        )

    def test_sqlite_bm25_weights_follow_column_order(self):
        # subject A=10.0, snippet C=2.0, from_email B=4.0, from_name B=4.0,
        # body_text D=1.0 - the exact config the applied mail schema uses.
        self.assertIn(
            "'bm25(10.0, 2.0, 4.0, 4.0, 1.0)'", MAIL_LIKE.sqlite_forward_sql()
        )

    def test_sqlite_triggers_sql_is_idempotent_variant(self):
        sql = CHAT_LIKE.sqlite_triggers_sql()
        self.assertIn("CREATE TRIGGER IF NOT EXISTS chat_message_fts_ai", sql)
        self.assertIn("VALUES ('rebuild')", sql)
        self.assertIn("'bm25(10.0)'", sql)
        self.assertNotIn("DROP TABLE", sql)


class RegistryTests(SimpleTestCase):
    def test_register_and_list(self):
        idx = FulltextIndex(table="reg_test_table", columns=("a",))
        with mock.patch.dict(schema._registered, {}, clear=True):
            schema.register_fulltext_index(idx)
            self.assertEqual(schema.registered_fulltext_indexes(), (idx,))
            # Re-registering the same table replaces, not duplicates
            # (ready() can run more than once in some test setups).
            schema.register_fulltext_index(idx)
            self.assertEqual(len(schema.registered_fulltext_indexes()), 1)


class RebuildHandlerTests(TransactionTestCase):
    """TransactionTestCase: executescript issues an implicit COMMIT which
    breaks TestCase's rollback isolation."""

    USER_IDX = FulltextIndex(table="auth_user", columns=("first_name",))

    def setUp(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        with connection.cursor() as c:
            c.executescript(self.USER_IDX.sqlite_forward_sql())

    def tearDown(self):
        with connection.cursor() as c:
            c.executescript(self.USER_IDX.sqlite_reverse_sql())

    def test_handler_restores_dropped_triggers(self):
        with connection.cursor() as c:
            c.execute("DROP TRIGGER IF EXISTS auth_user_fts_ai")
        with mock.patch.dict(
            schema._registered, {"auth_user": self.USER_IDX}, clear=True
        ):
            schema.rebuild_sqlite_fts_indexes(sender=None, using=connection.alias)
        get_user_model().objects.create_user(
            username="fts-reg", email="f@x.io", first_name="reindexable"
        )
        with connection.cursor() as c:
            c.execute(
                "SELECT rowid FROM auth_user_fts WHERE auth_user_fts MATCH %s",
                ('"reindexable"',),
            )
            self.assertIsNotNone(c.fetchone())

    def test_handler_skips_missing_fts_table(self):
        ghost = FulltextIndex(table="no_such_table", columns=("a",))
        with mock.patch.dict(schema._registered, {"no_such_table": ghost}, clear=True):
            # Must not raise even though no_such_table_fts does not exist.
            schema.rebuild_sqlite_fts_indexes(sender=None, using=connection.alias)

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase

from workspace.common.search import checks, documents
from workspace.common.search.documents import drop_document, index_document
from workspace.common.search.schema import DerivedFulltextIndex, Field

User = get_user_model()

# auth_user stands in for a real indexed table: derives to auth_user_fts,
# created for real in setUp so the SQLite branch runs against FTS5.
USER_DOC_FTS = DerivedFulltextIndex(
    table="auth_user",
    fields=(Field("title", weight="A"), Field("body", weight="C", cap=12)),
    fallback_fields=(),
    pk_column="id",
)


class DocumentValueTests(SimpleTestCase):
    def test_values_are_capped_and_missing_keys_become_empty(self):
        texts = documents.document_values(USER_DOC_FTS, {"body": "x" * 50})
        self.assertEqual(texts, ["", "x" * 12])

    def test_non_string_values_are_stringified(self):
        texts = documents.document_values(USER_DOC_FTS, {"title": 42, "body": None})
        self.assertEqual(texts, ["42", ""])


class SqliteDocumentTests(TestCase):
    """Exercise the real contentless FTS5 branch."""

    def setUp(self):
        if connection.vendor != "sqlite" or not documents.fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self.user = User.objects.create_user(username="doc", email="d@x.io")
        self.other = User.objects.create_user(username="doc2", email="d2@x.io")
        with connection.cursor() as c:
            c.execute(
                "CREATE VIRTUAL TABLE auth_user_fts USING fts5("
                "title, body, content='', contentless_delete=1, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            c.execute(
                "INSERT INTO auth_user_fts(auth_user_fts, rank) "
                "VALUES ('rank', 'bm25(10.0, 2.0)')"
            )

    def tearDown(self):
        with connection.cursor() as c:
            c.execute("DROP TABLE IF EXISTS auth_user_fts")

    def _rowids(self, match):
        with connection.cursor() as c:
            c.execute(
                "SELECT rowid FROM auth_user_fts WHERE auth_user_fts MATCH %s", [match]
            )
            return [row[0] for row in c.fetchall()]

    def test_indexed_document_is_searchable_by_body(self):
        index_document(USER_DOC_FTS, self.user.pk, {"title": "notes", "body": "kraken"})
        self.assertEqual(self._rowids('"kraken"'), [self.user.pk])

    def test_index_is_accent_insensitive(self):
        index_document(USER_DOC_FTS, self.user.pk, {"title": "Réunion"})
        self.assertEqual(self._rowids('"reunion"'), [self.user.pk])

    def test_reindexing_replaces_the_previous_document(self):
        index_document(USER_DOC_FTS, self.user.pk, {"body": "before"})
        index_document(USER_DOC_FTS, self.user.pk, {"body": "after"})
        self.assertEqual(self._rowids('"after"'), [self.user.pk])
        self.assertEqual(self._rowids('"before"'), [])

    def test_drop_document_removes_it(self):
        index_document(USER_DOC_FTS, self.user.pk, {"body": "ephemeral"})
        drop_document(USER_DOC_FTS, self.user.pk)
        self.assertEqual(self._rowids('"ephemeral"'), [])

    def test_body_is_capped(self):
        # cap=12: "aaaaaaaaaaaa needle" truncates before the needle.
        index_document(USER_DOC_FTS, self.user.pk, {"body": "a" * 12 + " needle"})
        self.assertEqual(self._rowids('"needle"'), [])

    def test_unknown_pk_writes_nothing(self):
        index_document(USER_DOC_FTS, 9_999_999, {"body": "ghost"})
        self.assertEqual(self._rowids('"ghost"'), [])

    def test_documents_are_independent_per_row(self):
        index_document(USER_DOC_FTS, self.user.pk, {"body": "alpha"})
        index_document(USER_DOC_FTS, self.other.pk, {"body": "beta"})
        self.assertEqual(self._rowids('"alpha"'), [self.user.pk])
        self.assertEqual(self._rowids('"beta"'), [self.other.pk])


class NoFts5Tests(TestCase):
    def test_write_is_a_silent_noop_without_fts5(self):
        # No FTS5 means content search is simply unavailable; writing a
        # document must not raise and must not touch a nonexistent table.
        with mock.patch.object(documents, "fts5_available", return_value=False):
            index_document(USER_DOC_FTS, 1, {"body": "x"})
            drop_document(USER_DOC_FTS, 1)


class PostgresStatementTests(SimpleTestCase):
    """The PG branch only runs on PostgreSQL, so assert on the SQL instead."""

    def test_update_binds_every_field_then_the_pk(self):
        sql = USER_DOC_FTS.pg_update_sql()
        self.assertEqual(sql.count("%s"), 3)
        self.assertNotIn("kraken", sql)

    def test_drop_is_a_noop_on_postgres(self):
        # The tsvector is a column of the row and dies with it; issuing an
        # UPDATE per deleted row would be pure waste on a cascade.
        conn = mock.Mock(vendor="postgresql")
        with mock.patch.object(documents, "connections", {"default": conn}):
            drop_document(USER_DOC_FTS, 1)
        conn.cursor.assert_not_called()


class SqliteCapabilityCheckTests(SimpleTestCase):
    def test_no_warning_on_a_recent_sqlite(self):
        with mock.patch.object(checks.sqlite3, "sqlite_version_info", (3, 50, 4)):
            self.assertEqual(checks.check_sqlite_fts_support(None), [])

    def test_warning_on_a_build_without_contentless_delete(self):
        with (
            mock.patch.object(checks.sqlite3, "sqlite_version_info", (3, 42, 0)),
            mock.patch.object(checks.sqlite3, "sqlite_version", "3.42.0"),
        ):
            warnings = checks.check_sqlite_fts_support(None)
        self.assertEqual([w.id for w in warnings], ["common.W001"])

    def test_no_warning_when_no_connection_is_sqlite(self):
        with mock.patch.object(
            checks,
            "connections",
            mock.Mock(all=lambda: [mock.Mock(vendor="postgresql")]),
        ):
            self.assertEqual(checks.check_sqlite_fts_support(None), [])

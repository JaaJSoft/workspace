from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from workspace.common import search as fts

User = get_user_model()


class Fts5MatchSanitizerTests(TestCase):
    def test_plain_words_are_quoted_and_anded(self):
        self.assertEqual(fts.to_fts5_match("hello world"), '"hello" "world"')

    def test_stray_operators_are_neutralized(self):
        # A lone quote or dash would raise "fts5: syntax error" if passed raw.
        self.assertEqual(
            fts.to_fts5_match('quarterly" -report'), '"quarterly" "report"'
        )

    def test_empty_query_yields_empty_string(self):
        self.assertEqual(fts.to_fts5_match("   "), "")

    def test_unicode_word_characters_are_kept(self):
        self.assertEqual(fts.to_fts5_match("café réunion"), '"café" "réunion"')


class FallbackBranchTests(TestCase):
    """When FTS5 is unavailable, apply_fulltext degrades to icontains."""

    @classmethod
    def setUpTestData(cls):
        cls.u1 = User.objects.create_user(
            username="a", email="a@x.io", first_name="Ada"
        )
        cls.u2 = User.objects.create_user(
            username="b", email="b@x.io", first_name="Bob"
        )

    def test_fallback_filters_and_annotates_rank(self):
        orig = fts.fts5_available
        fts.fts5_available = lambda: False
        try:
            qs = fts.apply_fulltext(
                User.objects.all(),
                "ada",
                pg_column="unused",
                sqlite_fts_table="unused",
                fallback_fields=("first_name", "username"),
            )
            rows = list(qs.order_by("-search_rank", "username"))
        finally:
            fts.fts5_available = orig
        self.assertEqual([r.username for r in rows], ["a"])
        self.assertEqual(rows[0].search_rank, 0.0)

    def test_blank_query_returns_no_rows_on_fallback(self):
        # An empty query would make icontains="" match every row; a
        # whitespace-only query strips to empty. Both must yield no rows.
        orig = fts.fts5_available
        fts.fts5_available = lambda: False
        try:
            for blank in ("", "   "):
                qs = fts.apply_fulltext(
                    User.objects.all(),
                    blank,
                    pg_column="unused",
                    sqlite_fts_table="unused",
                    fallback_fields=("first_name", "username"),
                )
                self.assertEqual(list(qs), [], f"blank query {blank!r} matched rows")
        finally:
            fts.fts5_available = orig


class SqliteFtsBranchTests(TestCase):
    """Exercise the real SQLite FTS5 branch against an ephemeral FTS table."""

    def setUp(self):
        if connection.vendor != "sqlite" or not fts.fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self.u1 = User.objects.create_user(
            username="alpha", email="a@x.io", first_name="Résumé Writer"
        )
        self.u2 = User.objects.create_user(
            username="beta", email="b@x.io", first_name="Plain Person"
        )
        with connection.cursor() as c:
            c.execute(
                "CREATE VIRTUAL TABLE tmp_user_fts USING fts5("
                "first_name, content='auth_user', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            c.execute("INSERT INTO tmp_user_fts(tmp_user_fts) VALUES('rebuild')")

    def tearDown(self):
        with connection.cursor() as c:
            c.execute("DROP TABLE IF EXISTS tmp_user_fts")

    def test_match_is_accent_insensitive_and_ranked(self):
        qs = fts.apply_fulltext(
            User.objects.all(),
            "resume",  # no accent; must still match "Résumé"
            pg_column="unused",
            sqlite_fts_table="tmp_user_fts",
            fallback_fields=("first_name",),
        ).order_by("-search_rank")
        rows = list(qs)
        self.assertEqual([r.username for r in rows], ["alpha"])
        self.assertIsInstance(rows[0].search_rank, float)

    def test_no_match_returns_empty(self):
        qs = fts.apply_fulltext(
            User.objects.all(),
            "zzzznomatch",
            pg_column="unused",
            sqlite_fts_table="tmp_user_fts",
            fallback_fields=("first_name",),
        )
        self.assertEqual(list(qs), [])

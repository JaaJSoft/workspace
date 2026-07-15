from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from workspace.common import search as fts
from workspace.common.search.fallback import IcontainsFulltext
from workspace.common.search.schema import FulltextIndex
from workspace.common.search.sqlite import to_fts5_match

User = get_user_model()

# fts_table derives to auth_user_fts; the SQLite branch tests create that
# exact table so the derived name resolves.
USER_FTS = FulltextIndex(table="auth_user", columns=("first_name", "username"))
USER_NAME_FTS = FulltextIndex(table="auth_user", columns=("first_name",))


class Fts5MatchSanitizerTests(TestCase):
    def test_plain_words_are_quoted_and_anded(self):
        self.assertEqual(to_fts5_match("hello world"), '"hello" "world"')

    def test_stray_operators_are_neutralized(self):
        # A lone quote or dash would raise "fts5: syntax error" if passed raw.
        self.assertEqual(to_fts5_match('quarterly" -report'), '"quarterly" "report"')

    def test_empty_query_yields_empty_string(self):
        self.assertEqual(to_fts5_match("   "), "")

    def test_unicode_word_characters_are_kept(self):
        self.assertEqual(to_fts5_match("café réunion"), '"café" "réunion"')


class Fts5ProbeTests(TestCase):
    def test_probe_failure_is_not_cached(self):
        # A transient error (locked db, dropped connection) must not pin the
        # degraded fallback for the whole process lifetime; only a real
        # probe result may be cached.
        orig_cache = fts._fts5_available_cache
        fts._fts5_available_cache = None
        try:
            with mock.patch("workspace.common.search.connection") as conn:
                conn.cursor.side_effect = RuntimeError("boom")
                self.assertFalse(fts.fts5_available())
            self.assertIsNone(fts._fts5_available_cache)
        finally:
            fts._fts5_available_cache = orig_cache


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
            qs = fts.apply_fulltext(User.objects.all(), "ada", index=USER_FTS)
            rows = list(qs.order_by("-search_rank", "username"))
        finally:
            fts.fts5_available = orig
        self.assertEqual([r.username for r in rows], ["a"])
        self.assertEqual(rows[0].search_rank, 0.0)

    def test_empty_fallback_fields_return_no_rows(self):
        # A FulltextIndex rejects empty columns, but the strategy keeps its
        # own fail-safe: an empty Q() would match every row.
        qs = IcontainsFulltext().apply(User.objects.all(), "ada", fallback_fields=())
        self.assertEqual(list(qs), [])

    def test_blank_query_returns_no_rows_on_fallback(self):
        # An empty query would make icontains="" match every row; a
        # whitespace-only query strips to empty. Both must yield no rows.
        orig = fts.fts5_available
        fts.fts5_available = lambda: False
        try:
            for blank in ("", "   "):
                qs = fts.apply_fulltext(User.objects.all(), blank, index=USER_FTS)
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
                "CREATE VIRTUAL TABLE auth_user_fts USING fts5("
                "first_name, content='auth_user', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            c.execute("INSERT INTO auth_user_fts(auth_user_fts) VALUES('rebuild')")

    def tearDown(self):
        with connection.cursor() as c:
            c.execute("DROP TABLE IF EXISTS auth_user_fts")

    def test_match_is_accent_insensitive_and_ranked(self):
        qs = fts.apply_fulltext(
            User.objects.all(),
            "resume",  # no accent; must still match "Résumé"
            index=USER_NAME_FTS,
        ).order_by("-search_rank")
        rows = list(qs)
        self.assertEqual([r.username for r in rows], ["alpha"])
        self.assertIsInstance(rows[0].search_rank, float)

    def test_queryset_filters_apply_before_any_result_cap(self):
        # SQLite serves small production environments, so a queryset's
        # access-control filters must apply INSIDE the FTS query. A global
        # top-N cap taken before those filters can starve a user out of
        # their own matches when other rows rank higher. The cap is patched
        # tiny (create=True keeps the patch valid once the cap is gone) to
        # reproduce that starvation without thousands of rows.
        for i in range(3):
            User.objects.create_user(
                username=f"noisy{i}",
                email=f"n{i}@x.io",
                first_name="starve starve starve starve",
            )
        target = User.objects.create_user(
            username="target", email="t@x.io", first_name="starve"
        )
        with connection.cursor() as c:
            c.execute("INSERT INTO auth_user_fts(auth_user_fts) VALUES('rebuild')")

        restricted = User.objects.filter(pk=target.pk)
        with mock.patch.object(fts, "_SQLITE_SAFETY_LIMIT", 2, create=True):
            qs = fts.apply_fulltext(restricted, "starve", index=USER_NAME_FTS)
            self.assertEqual([u.username for u in qs], ["target"])

    def test_no_match_returns_empty(self):
        qs = fts.apply_fulltext(User.objects.all(), "zzzznomatch", index=USER_NAME_FTS)
        self.assertEqual(list(qs), [])

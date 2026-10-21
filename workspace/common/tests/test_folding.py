from django.contrib.auth.models import Group
from django.db import NotSupportedError, connection
from django.db.models import F
from django.test import SimpleTestCase, TestCase

from workspace.common.folding import Fold


class FoldedContainsTests(TestCase):
    def _matches(self, name, needle):
        Group.objects.create(name=name)
        return Group.objects.filter(name__ficontains=needle).exists()

    def test_a_query_without_accents_finds_the_accented_text(self):
        self.assertTrue(self._matches("François Dupré", "francois dupre"))

    def test_an_accented_query_finds_the_plain_text(self):
        self.assertTrue(self._matches("Francois", "çois"))

    def test_folds_non_ascii_case(self):
        # SQLite's LIKE folds ASCII only: icontains misses this one.
        self.assertTrue(self._matches("Élodie", "élodie"))

    def test_folds_sharp_s(self):
        self.assertTrue(self._matches("Straße", "STRASSE"))

    def test_splits_ligatures(self):
        self.assertTrue(self._matches("ﬁnance", "finance"))

    def test_does_not_match_other_text(self):
        self.assertFalse(self._matches("François", "frank"))

    def test_like_wildcards_in_the_needle_are_literal(self):
        Group.objects.create(name="100 percent")
        self.assertFalse(Group.objects.filter(name__ficontains="100%").exists())
        self.assertFalse(Group.objects.filter(name__ficontains="1_0").exists())
        Group.objects.create(name="50% off")
        self.assertTrue(Group.objects.filter(name__ficontains="50%").exists())

    def test_rhs_can_be_an_expression(self):
        Group.objects.create(name="Hélène")
        self.assertTrue(Group.objects.filter(name__ficontains=F("name")).exists())

    def test_annotation_returns_the_folded_text(self):
        Group.objects.create(name="Ångström Hélène")
        folded = Group.objects.annotate(folded=Fold("name")).get().folded
        self.assertEqual(folded, "angstrom helene")


class FoldSqlTests(SimpleTestCase):
    """The default suite runs on SQLite: pin the PostgreSQL form by its SQL."""

    def _sql(self, method):
        query = Group.objects.filter(name__ficontains="x").query
        compiler = query.get_compiler(connection=connection)
        return method(Fold(F("name")).resolve_expression(query), compiler, connection)

    def test_postgresql_folds_with_f_unaccent(self):
        sql, _ = self._sql(Fold.as_postgresql)
        self.assertEqual(sql, 'f_unaccent(lower("auth_group"."name"))')

    def test_sqlite_folds_with_the_registered_function(self):
        sql, _ = self._sql(Fold.as_sqlite)
        self.assertEqual(sql, 'fold_text("auth_group"."name")')

    def test_other_backends_are_refused(self):
        with self.assertRaises(NotSupportedError):
            self._sql(Fold.as_sql)

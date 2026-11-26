from unittest import skipUnless

from django.contrib.auth.models import Group
from django.db import NotSupportedError, connection
from django.db.models import F
from django.db.models.functions import Collate
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

    @skipUnless(connection.vendor == "postgresql", "collation C is PostgreSQL's")
    def test_folds_accented_capitals_under_the_c_collation(self):
        # Under "C", PostgreSQL's lower() folds ASCII only, so lowering first
        # would leave "É" alone and unaccent would turn it into "E".
        Group.objects.create(name="Élodie")
        folded = Group.objects.annotate(folded=Fold(Collate("name", "C"))).get().folded
        self.assertEqual(folded, "elodie")


class FoldSqlTests(SimpleTestCase):
    """The default suite runs on SQLite: pin the PostgreSQL form by its SQL."""

    def _sql(self, method):
        query = Group.objects.filter(name__ficontains="x").query
        compiler = query.get_compiler(connection=connection)
        return method(Fold(F("name")).resolve_expression(query), compiler, connection)

    def test_postgresql_folds_with_f_unaccent(self):
        sql, _ = self._sql(Fold.as_postgresql)
        self.assertEqual(sql, 'lower(f_unaccent("auth_group"."name"))')

    def test_sqlite_folds_with_the_registered_function(self):
        sql, _ = self._sql(Fold.as_sqlite)
        self.assertEqual(sql, 'fold_text("auth_group"."name")')

    def test_other_backends_are_refused(self):
        with self.assertRaises(NotSupportedError):
            self._sql(Fold.as_sql)


class FoldedExactTests(TestCase):
    def _matches(self, name, value):
        Group.objects.create(name=name)
        return Group.objects.filter(name__fiexact=value).exists()

    def test_case_and_accents_are_ignored_both_ways(self):
        self.assertTrue(self._matches("Réunion", "reunion"))
        self.assertTrue(self._matches("elodie", "ÉLODIE"))

    def test_the_whole_value_must_match(self):
        self.assertFalse(self._matches("Comptabilité 2026", "compta"))
        self.assertFalse(self._matches("compta", "compta perso"))

    def test_like_wildcards_are_ordinary_characters(self):
        self.assertFalse(self._matches("100 percent", "100%"))
        self.assertFalse(self._matches("a-b", "a_b"))

    def test_the_lookup_crosses_a_relation(self):
        from django.contrib.auth.models import Permission

        group = Group.objects.create(name="editors")
        group.permissions.add(Permission.objects.get(codename="add_group"))
        self.assertTrue(
            Group.objects.filter(permissions__codename__fiexact="ADD_GROUP").exists()
        )
        self.assertFalse(
            Group.objects.filter(permissions__codename__fiexact="add").exists()
        )

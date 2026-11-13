import json
import uuid
from datetime import date, timedelta
from unittest import skipUnless

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import OperationalError, connection, models
from django.db.models import (
    DateField,
    FloatField,
    GenericIPAddressField,
    IntegerField,
    Value,
)
from django.db.models.functions import Concat
from django.db.models.lookups import In
from django.test import TestCase, TransactionTestCase
from django.test.utils import isolate_apps

from workspace.common import in_lists
from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.common.uuids import uuid_v7_or_v4

PARAMETER_LIMIT = 20
ON_SQLITE = skipUnless(connection.vendor == "sqlite", "SQLite's form of a long list")
ON_POSTGRESQL = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL's form of a long list"
)

# Enough values to take the long form: past the lowered limit on SQLite, past
# the fixed threshold on PostgreSQL, which has no limit to lower.
LONG = PARAMETER_LIMIT * 3 if connection.vendor == "sqlite" else 1100


class LongInListTests(TestCase):
    """``__in`` with more values than a statement binds.

    On SQLite the limit is lowered around each query, so sixty rows stand in
    for the 32766 values it takes in production.
    """

    @classmethod
    def setUpTestData(cls):
        cls.groups = Group.objects.bulk_create(
            Group(name=f"group {index}") for index in range(LONG)
        )
        cls.pks = [group.pk for group in cls.groups]

    def _names(self, queryset):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            return set(queryset.values_list("name", flat=True))

    @ON_SQLITE
    def test_the_lowered_limit_refuses_a_statement_that_binds_too_much(self):
        # The control for every test below: without it they would pass on a
        # limit that was never lowered.
        placeholders = ", ".join(["%s"] * len(self.pks))
        with (
            sqlite_parameter_limit(PARAMETER_LIMIT),
            self.assertRaises(OperationalError),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                f"SELECT COUNT(*) FROM auth_group WHERE id IN ({placeholders})",
                self.pks,
            )

    def test_matches_integers(self):
        found = Group.objects.filter(pk__in=self.pks)
        self.assertEqual(len(self._names(found)), len(self.pks))

    def test_matches_only_the_listed_rows(self):
        found = Group.objects.filter(pk__in=self.pks[:-1])
        self.assertNotIn(self.groups[-1].name, self._names(found))
        self.assertEqual(len(self._names(found)), len(self.pks) - 1)

    def test_text_reaches_the_database_unchanged(self):
        names = ["Élodie", "🎉 party", 'say "hi"', "back\\slash", "it's", "a,b", "{}"]
        names += ["line\nbreak", "42", ""]
        if connection.vendor == "sqlite":
            # PostgreSQL text cannot hold a NUL at all.
            names.append("nul\x00inside")
        Group.objects.bulk_create(Group(name=name) for name in names)
        wanted = names + [f"absent {index}" for index in range(LONG)]
        self.assertEqual(self._names(Group.objects.filter(name__in=wanted)), set(names))

    def test_matches_a_foreign_key_by_column_relation_or_instance(self):
        content_types = list(ContentType.objects.all())
        self.assertGreater(len(content_types), PARAMETER_LIMIT)
        expected = Permission.objects.count()
        self.assertGreater(expected, 0)
        pks = [content_type.pk for content_type in content_types]
        filters = {
            "content_type_id__in": pks,
            "content_type__in": pks,
            "content_type__in ": content_types,
        }
        for lookup, values in filters.items():
            with self.subTest(lookup=lookup), sqlite_parameter_limit(PARAMETER_LIMIT):
                found = Permission.objects.filter(**{lookup.strip(): values}).count()
                self.assertEqual(found, expected)

    def test_exclude_keeps_the_other_rows(self):
        excluded = Group.objects.exclude(pk__in=self.pks[1:])
        self.assertEqual(self._names(excluded), {"group 0"})

    def test_none_and_repeated_values_change_nothing(self):
        found = Group.objects.filter(pk__in=[None, *self.pks, *self.pks])
        self.assertEqual(len(self._names(found)), len(self.pks))

    def test_takes_any_collection(self):
        for values in (set(self.pks), tuple(self.pks), dict.fromkeys(self.pks)):
            with self.subTest(type=type(values).__name__):
                found = Group.objects.filter(pk__in=values)
                self.assertEqual(len(self._names(found)), len(self.pks))

    def test_values_are_converted_like_the_field_does(self):
        found = Group.objects.filter(pk__in=[str(pk) for pk in self.pks])
        self.assertEqual(len(self._names(found)), len(self.pks))

    def test_combines_with_other_filters_and_ordering(self):
        found = Group.objects.filter(pk__in=self.pks, name__endswith="3").order_by(
            "-name"
        )
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            names = [group.name for group in found[:2]]
        self.assertEqual(names, sorted(names, reverse=True))
        self.assertTrue(all(name.endswith("3") for name in names), names)

    def test_two_long_lists_in_one_statement(self):
        names = [group.name for group in self.groups]
        found = Group.objects.filter(pk__in=self.pks, name__in=names)
        self.assertEqual(len(self._names(found)), len(self.pks))

    def test_updates_more_rows_than_a_statement_binds(self):
        renamed = Concat(Value("renamed "), "name")
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            updated = Group.objects.filter(pk__in=self.pks[1:]).update(name=renamed)
        self.assertEqual(updated, len(self.pks) - 1)
        kept = Group.objects.exclude(name__startswith="renamed")
        self.assertEqual(self._names(kept), {"group 0"})

    def test_prefetches_for_more_rows_than_a_statement_binds(self):
        permission = Permission.objects.first()
        permission.group_set.set(self.groups)
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            groups = list(Group.objects.prefetch_related("permissions"))
        self.assertEqual(len(groups), len(self.groups))
        with self.assertNumQueries(0):
            for group in groups:
                self.assertEqual(list(group.permissions.all()), [permission])


@isolate_apps("workspace.common")
class LongInListOfUuidsTests(TransactionTestCase):
    """UUID keys, which SQLite stores as text and PostgreSQL natively.

    TransactionTestCase because the schema editor cannot run inside the test's
    transaction on SQLite.
    """

    def setUp(self):
        class Node(models.Model):
            uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4)
            parent = models.ForeignKey("self", null=True, on_delete=models.CASCADE)

            class Meta:
                app_label = "common"
                db_table = "common_test_in_lists_node"

            def __str__(self):
                return str(self.uuid)

        self.Node = Node
        with connection.schema_editor() as editor:
            editor.create_model(Node)
        self.root = Node.objects.create()
        self.child = Node.objects.create(parent=self.root)
        self.other = Node.objects.create()
        self.strangers = [uuid.uuid4() for _ in range(LONG)]

    def tearDown(self):
        with connection.schema_editor() as editor:
            editor.delete_model(self.Node)

    def _found(self, queryset):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            return list(queryset)

    def test_matches_uuids_given_as_objects_or_text(self):
        wanted = [*self.strangers, self.root.pk]
        for values in (wanted, [str(value) for value in wanted]):
            with self.subTest(type=type(values[0]).__name__):
                found = self._found(self.Node.objects.filter(pk__in=values))
                self.assertEqual(found, [self.root])

    def test_matches_a_uuid_foreign_key(self):
        wanted = [*self.strangers, self.root.pk]
        found = self._found(self.Node.objects.filter(parent_id__in=wanted))
        self.assertEqual(found, [self.child])

    def test_exclude_keeps_the_rows_whose_column_is_null(self):
        wanted = [*self.strangers, self.root.pk]
        excluded = self._found(self.Node.objects.exclude(parent_id__in=wanted))
        self.assertCountEqual(excluded, [self.root, self.other])


@ON_SQLITE
class SqliteLongListSqlTests(TestCase):
    """Which lists take the JSON form, read off the compiled statement."""

    def _sql(self, queryset):
        return queryset.query.sql_with_params()

    def _longest_plain_list(self):
        return connection.features.max_query_params // 64

    def test_a_long_list_is_one_parameter(self):
        values = list(range(self._longest_plain_list() + 1))
        sql, params = self._sql(Group.objects.filter(pk__in=values))
        self.assertIn('"auth_group"."id" IN (SELECT value FROM json_each(%s))', sql)
        self.assertEqual([json.loads(param) for param in params], [values])

    def test_a_short_list_keeps_its_placeholders(self):
        values = list(range(self._longest_plain_list()))
        sql, params = self._sql(Group.objects.filter(pk__in=values))
        self.assertNotIn("json_each", sql)
        self.assertEqual(list(params), values)

    def test_the_threshold_follows_the_connection_limit(self):
        with sqlite_parameter_limit(640):
            sql, _ = self._sql(Group.objects.filter(pk__in=range(10)))
            self.assertNotIn("json_each", sql)
            sql, _ = self._sql(Group.objects.filter(pk__in=range(11)))
            self.assertIn("json_each", sql)

    def test_a_subquery_stays_a_subquery(self):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            sql, params = self._sql(
                Permission.objects.filter(group__in=Group.objects.all())
            )
        self.assertNotIn("json_each", sql)
        self.assertEqual(list(params), [])

    def test_values_json_would_alter_keep_their_placeholders(self):
        values = [index / 7 for index in range(PARAMETER_LIMIT * 2)]
        lookup = In(Value(1.5, output_field=FloatField()), values)
        compiler = Group.objects.all().query.get_compiler(connection=connection)
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            sql, params = in_lists.as_sqlite(lookup, compiler, connection)
        self.assertNotIn("json_each", sql)
        self.assertEqual(list(params)[1:], values)

    def test_an_empty_list_matches_nothing(self):
        self.assertFalse(Group.objects.filter(pk__in=[]).exists())
        Group.objects.create(name="only")
        self.assertEqual(Group.objects.exclude(pk__in=[None]).count(), 1)


@ON_POSTGRESQL
class PostgresqlLongListSqlTests(TestCase):
    """Which lists take the array form, read off the compiled statement."""

    def _sql(self, queryset):
        return queryset.query.sql_with_params()

    def _compile(self, output_field, values):
        lookup = In(Value(values[0], output_field=output_field), values)
        compiler = Group.objects.all().query.get_compiler(connection=connection)
        return in_lists.as_postgresql(lookup, compiler, connection)

    def test_a_long_list_is_one_array(self):
        values = list(range(LONG))
        sql, params = self._sql(Group.objects.filter(pk__in=values))
        self.assertIn('"auth_group"."id" = ANY(%s)', sql)
        self.assertEqual(list(params), [values])

    def test_a_short_list_keeps_its_placeholders(self):
        values = list(range(1023))
        sql, params = self._sql(Group.objects.filter(pk__in=values))
        self.assertNotIn("ANY", sql)
        self.assertEqual(list(params), values)

    def test_text_goes_to_a_text_column_as_an_array(self):
        values = [f"name {index}" for index in range(LONG)]
        sql, params = self._sql(Group.objects.filter(name__in=values))
        self.assertIn("= ANY(%s)", sql)
        self.assertEqual(list(params), [values])

    def test_text_for_another_column_type_keeps_its_placeholders(self):
        # An array is typed by its elements: inet has no operator against text[].
        values = [f"10.0.{index // 256}.{index % 256}" for index in range(LONG)]
        sql, _ = self._compile(GenericIPAddressField(), values)
        self.assertNotIn("ANY", sql)

    def test_other_value_types_keep_their_placeholders(self):
        days = [date(2026, 1, 1) + timedelta(days=index) for index in range(LONG)]
        floats = [index / 7 for index in range(LONG)]
        for output_field, values in ((DateField(), days), (FloatField(), floats)):
            with self.subTest(type=type(values[0]).__name__):
                sql, _ = self._compile(output_field, values)
                self.assertNotIn("ANY", sql)

    def test_an_integer_past_the_size_of_its_column_keeps_the_placeholders(self):
        # Django sizes each value after the column, and an array takes that
        # size: one value past it would fail the statement.
        fitting = list(range(LONG))
        sql, _ = self._compile(IntegerField(), fitting)
        self.assertIn("= ANY(%s)", sql)
        sql, _ = self._compile(IntegerField(), [*fitting, 2**40])
        self.assertNotIn("ANY", sql)

    def test_integers_of_every_size_share_one_array(self):
        Group.objects.create(name="only")
        values = [*range(LONG), 2**40, -(2**40)]
        found = Group.objects.filter(pk__in=values)
        self.assertEqual(found.count(), Group.objects.filter(pk__lt=LONG).count())

    def test_a_subquery_stays_a_subquery(self):
        sql, _ = self._sql(Permission.objects.filter(group__in=Group.objects.all()))
        self.assertNotIn("ANY", sql)

import json
import uuid
from unittest import skipUnless

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import NotSupportedError, OperationalError, connection, models
from django.db.models import FloatField, Value
from django.db.models.functions import Concat
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import isolate_apps

from workspace.common.in_array import InArray
from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.common.uuids import uuid_v7_or_v4

PARAMETER_LIMIT = 20


class InArrayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.groups = Group.objects.bulk_create(
            Group(name=f"group {index}") for index in range(PARAMETER_LIMIT * 3)
        )
        cls.pks = [group.pk for group in cls.groups]

    def _names(self, queryset):
        return set(queryset.values_list("name", flat=True))

    def test_matches_integers(self):
        found = Group.objects.filter(pk__in_array=self.pks[:2])
        self.assertEqual(self._names(found), {"group 0", "group 1"})

    def test_matches_text(self):
        found = Group.objects.filter(name__in_array=["group 3", "no such group"])
        self.assertEqual(self._names(found), {"group 3"})

    def test_text_reaches_the_database_unchanged(self):
        names = ["Élodie", "🎉 party", 'say "hi"', "back\\slash", "it's", "a,b", "{}"]
        Group.objects.bulk_create(Group(name=name) for name in names)
        found = Group.objects.filter(name__in_array=names)
        self.assertEqual(self._names(found), set(names))

    def test_text_that_looks_like_a_number_only_matches_text(self):
        Group.objects.create(name="42")
        self.assertEqual(Group.objects.filter(name__in_array=["42"]).count(), 1)

    def test_matches_a_foreign_key_by_column_and_by_relation(self):
        content_type = ContentType.objects.get_for_model(Group)
        expected = Permission.objects.filter(content_type=content_type).count()
        self.assertGreater(expected, 0)
        for lookup in ("content_type_id__in_array", "content_type__in_array"):
            with self.subTest(lookup=lookup):
                found = Permission.objects.filter(**{lookup: [content_type.pk]})
                self.assertEqual(found.count(), expected)

    def test_an_empty_list_matches_nothing(self):
        self.assertFalse(Group.objects.filter(pk__in_array=[]).exists())

    def test_excluding_an_empty_list_excludes_nothing(self):
        excluded = Group.objects.exclude(pk__in_array=[])
        self.assertEqual(excluded.count(), len(self.pks))

    def test_exclude_keeps_the_other_rows(self):
        excluded = Group.objects.exclude(pk__in_array=self.pks[1:])
        self.assertEqual(self._names(excluded), {"group 0"})

    def test_none_matches_nothing(self):
        found = Group.objects.filter(pk__in_array=[None, self.pks[0]])
        self.assertEqual(self._names(found), {"group 0"})
        self.assertFalse(Group.objects.filter(pk__in_array=[None]).exists())

    def test_takes_any_iterable(self):
        for values in (set(self.pks[:2]), tuple(self.pks[:2]), iter(self.pks[:2])):
            with self.subTest(type=type(values).__name__):
                found = Group.objects.filter(pk__in_array=values)
                self.assertEqual(found.count(), 2)

    def test_a_repeated_value_matches_its_row_once(self):
        found = Group.objects.filter(pk__in_array=[self.pks[0]] * 3)
        self.assertEqual(found.count(), 1)

    def test_values_are_converted_like_the_field_does(self):
        found = Group.objects.filter(pk__in_array=[str(self.pks[0])])
        self.assertEqual(self._names(found), {"group 0"})

    def test_combines_with_other_filters_and_ordering(self):
        found = Group.objects.filter(
            pk__in_array=self.pks[:5], name__endswith="3"
        ).order_by("-name")
        self.assertEqual([group.name for group in found], ["group 3"])

    def test_a_queryset_is_refused(self):
        with self.assertRaisesMessage(TypeError, "use __in for a subquery"):
            Group.objects.filter(pk__in_array=Group.objects.values("pk"))

    def test_a_bare_string_is_refused(self):
        with self.assertRaisesMessage(TypeError, "takes a list of values"):
            Group.objects.filter(name__in_array="group 1")

    @skipUnless(connection.vendor == "sqlite", "the parameter limit is SQLite's")
    def test_the_lowered_limit_refuses_a_plain_in(self):
        # The control for the tests below: without it they would pass on a
        # limit that was never lowered.
        with (
            sqlite_parameter_limit(PARAMETER_LIMIT),
            self.assertRaises(OperationalError),
        ):
            Group.objects.filter(pk__in=self.pks).count()

    def test_reads_more_values_than_a_statement_binds(self):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            found = Group.objects.filter(pk__in_array=self.pks).count()
        self.assertEqual(found, len(self.pks))

    def test_updates_more_rows_than_a_statement_binds(self):
        renamed = Concat(Value("renamed "), "name")
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            updated = Group.objects.filter(pk__in_array=self.pks[1:]).update(
                name=renamed
            )
        self.assertEqual(updated, len(self.pks) - 1)
        self.assertEqual(
            self._names(Group.objects.exclude(name__startswith="renamed")), {"group 0"}
        )


@isolate_apps("workspace.common")
class InArrayUuidTests(TransactionTestCase):
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
                db_table = "common_test_in_array_node"

            def __str__(self):
                return str(self.uuid)

        self.Node = Node
        with connection.schema_editor() as editor:
            editor.create_model(Node)
        self.root = Node.objects.create()
        self.child = Node.objects.create(parent=self.root)
        self.other = Node.objects.create()

    def tearDown(self):
        with connection.schema_editor() as editor:
            editor.delete_model(self.Node)

    def test_matches_uuids_given_as_objects_or_text(self):
        for values in ([self.root.pk, uuid.uuid4()], [str(self.root.pk)]):
            with self.subTest(type=type(values[0]).__name__):
                found = self.Node.objects.filter(pk__in_array=values)
                self.assertEqual(list(found), [self.root])

    def test_matches_a_uuid_foreign_key(self):
        found = self.Node.objects.filter(parent_id__in_array=[self.root.pk])
        self.assertEqual(list(found), [self.child])

    def test_exclude_keeps_the_rows_whose_column_is_null(self):
        excluded = self.Node.objects.exclude(parent_id__in_array=[self.root.pk])
        self.assertCountEqual(excluded, [self.root, self.other])

    def test_reads_more_uuids_than_a_statement_binds(self):
        values = [uuid.uuid4() for _ in range(PARAMETER_LIMIT * 3)] + [self.other.pk]
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            found = list(self.Node.objects.filter(pk__in_array=values))
        self.assertEqual(found, [self.other])


class InArraySqlTests(SimpleTestCase):
    """The default suite runs on SQLite: pin the PostgreSQL form by its SQL."""

    def _compile(self, method, lookup=None):
        query = Group.objects.all().query
        compiler = query.get_compiler(connection=connection)
        if lookup is None:
            lookup = Group.objects.filter(pk__in_array=[1, 2]).query.where.children[0]
        return method(lookup, compiler, connection)

    def test_postgresql_compares_with_one_array(self):
        sql, params = self._compile(InArray.as_postgresql)
        self.assertEqual(sql, '"auth_group"."id" = ANY(%s)')
        self.assertEqual(list(params), [[1, 2]])

    def test_sqlite_reads_one_json_array(self):
        sql, params = self._compile(InArray.as_sqlite)
        self.assertEqual(sql, '"auth_group"."id" IN (SELECT value FROM json_each(%s))')
        self.assertEqual([json.loads(param) for param in params], [[1, 2]])

    def test_other_backends_are_refused(self):
        with self.assertRaises(NotSupportedError):
            self._compile(InArray.as_sql)

    def test_sqlite_refuses_values_json_would_alter(self):
        lookup = InArray(Value(1.5, output_field=FloatField()), [0.1])
        with self.assertRaisesMessage(NotSupportedError, "got float"):
            self._compile(InArray.as_sqlite, lookup)

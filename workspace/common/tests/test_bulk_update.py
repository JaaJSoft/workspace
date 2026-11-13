from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import OperationalError, connection, transaction
from django.db.models import F, QuerySet
from django.db.models.functions import Upper
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from workspace.common.bulk_update import widest_batch
from workspace.common.tests.parameter_limit import sqlite_parameter_limit

User = get_user_model()

PARAMETER_LIMIT = 60
ON_SQLITE = skipUnless(connection.vendor == "sqlite", "the parameter limit is SQLite's")
# Three, so that Django's own sizing overruns whatever the limit divides by.
FIELDS = ["name", "codename", "content_type"]
DEFAULT_BATCH = "workspace.common.bulk_update.DEFAULT_BATCH"


def _connection(limit):
    return SimpleNamespace(features=SimpleNamespace(max_query_params=limit))


class WidestBatchTests(SimpleTestCase):
    def test_counts_a_key_and_a_value_per_field_and_a_key_for_the_filter(self):
        # 2 * fields + 1 parameters per object.
        self.assertEqual(widest_batch(Group, ["name"], _connection(32766)), 10922)
        fields = ["name", "codename"]
        self.assertEqual(widest_batch(Permission, fields, _connection(32766)), 6553)
        fields = ["a", "b", "c", "d", "e"]
        self.assertEqual(widest_batch(Permission, fields, _connection(32766)), 2978)

    def test_a_limit_below_one_object_still_sends_one(self):
        self.assertEqual(
            widest_batch(Permission, ["name", "codename"], _connection(3)), 1
        )

    def test_a_database_without_a_limit_is_left_alone(self):
        self.assertIsNone(widest_batch(Group, ["name"], _connection(None)))


class PermissionsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.content_type = ContentType.objects.get_for_model(Group)
        cls.other_content_type = ContentType.objects.get_for_model(Permission)
        cls.permissions = Permission.objects.bulk_create(
            Permission(name=f"p{i}", codename=f"c{i}", content_type=cls.content_type)
            for i in range(100)
        )

    def _renamed(self):
        return Permission.objects.filter(
            name__startswith="renamed ", codename__startswith="renamed_"
        ).count()


class PlainValuesTests(PermissionsTestCase):
    """Plain values, which go out as one table of them."""

    def _rename(self):
        for permission in self.permissions:
            permission.name = f"renamed {permission.name}"
            permission.codename = f"renamed_{permission.codename}"

    def test_writes_every_field_of_every_object(self):
        self._rename()
        for permission in self.permissions:
            permission.content_type = self.other_content_type

        updated = Permission.objects.bulk_update(self.permissions, FIELDS)

        self.assertEqual(updated, 100)
        self.assertEqual(self._renamed(), 100)
        moved = Permission.objects.filter(
            pk__in=[permission.pk for permission in self.permissions],
            content_type=self.other_content_type,
        )
        self.assertEqual(moved.count(), 100)

    def test_is_one_statement_joined_on_a_table_of_values(self):
        self._rename()
        with CaptureQueriesContext(connection) as queries:
            Permission.objects.bulk_update(self.permissions, FIELDS)
        self.assertEqual(len(queries), 1)
        statement = queries[0]["sql"]
        self.assertIn("VALUES", statement)
        self.assertNotIn("CASE", statement)

    def test_text_reaches_the_database_unchanged(self):
        texts = ["Élodie 🎉", 'say "hi"', "it's", "back\\slash", "50% %s %(x)s", ""]
        changed = self.permissions[: len(texts)]
        for permission, text in zip(changed, texts, strict=True):
            permission.name = text
        Permission.objects.bulk_update(changed, ["name"])
        stored = Permission.objects.filter(pk__in=[p.pk for p in changed])
        self.assertCountEqual(stored.values_list("name", flat=True), texts)

    def test_nulls_booleans_and_dates_round_trip(self):
        seen = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
        users = User.objects.bulk_create(
            User(username=f"user-{i}", last_login=seen) for i in range(4)
        )
        users[0].last_login, users[0].is_active = None, False
        users[1].last_login, users[1].is_active = None, True
        users[2].last_login = datetime(2027, 1, 2, 3, 4, tzinfo=UTC)
        users[2].is_active = False

        User.objects.bulk_update(users, ["last_login", "is_active"])

        stored = {
            user.username: (user.last_login, user.is_active)
            for user in User.objects.filter(pk__in=[user.pk for user in users])
        }
        self.assertEqual(
            stored,
            {
                "user-0": (None, False),
                "user-1": (None, True),
                "user-2": (datetime(2027, 1, 2, 3, 4, tzinfo=UTC), False),
                "user-3": (seen, True),
            },
        )

    def test_more_objects_than_one_statement_binds(self):
        self._rename()
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            updated = Permission.objects.bulk_update(self.permissions, FIELDS)
        self.assertEqual(updated, 100)
        self.assertEqual(self._renamed(), 100)

    def test_fields_given_as_any_iterable(self):
        self._rename()
        Permission.objects.bulk_update(self.permissions, (name for name in FIELDS))
        self.assertEqual(self._renamed(), 100)

    def test_a_batch_size_of_the_caller_is_kept(self):
        self._rename()
        with self.assertNumQueries(10):
            Permission.objects.bulk_update(self.permissions, FIELDS, batch_size=10)
        self.assertEqual(self._renamed(), 100)

    def test_an_object_whose_row_is_gone_is_not_counted(self):
        self._rename()
        Permission.objects.filter(pk=self.permissions[0].pk).delete()
        updated = Permission.objects.bulk_update(self.permissions, FIELDS)
        self.assertEqual(updated, 99)

    def test_no_object_writes_nothing(self):
        with self.assertNumQueries(0):
            self.assertEqual(Permission.objects.bulk_update([], FIELDS), 0)

    def test_a_related_object_never_saved_on_a_field_left_out_is_ignored(self):
        permission = self.permissions[0]
        permission.name = "renamed"
        permission.content_type = ContentType(app_label="nowhere", model="nothing")

        updated = Permission.objects.bulk_update([permission], ["name"])

        self.assertEqual(updated, 1)
        stored = Permission.objects.get(pk=permission.pk)
        self.assertEqual(stored.name, "renamed")
        self.assertEqual(stored.content_type, self.content_type)


class RefusedCallsTests(PermissionsTestCase):
    """What Django refuses is still refused, in its own words."""

    def test_an_invalid_batch_size(self):
        for batch_size in (0, -1):
            with (
                self.subTest(batch_size=batch_size),
                self.assertRaisesMessage(ValueError, "positive integer"),
            ):
                Permission.objects.bulk_update(
                    self.permissions, ["name"], batch_size=batch_size
                )

    def test_no_field(self):
        with self.assertRaisesMessage(ValueError, "Field names must be given"):
            Permission.objects.bulk_update(self.permissions, [])

    def test_the_primary_key(self):
        with self.assertRaisesMessage(ValueError, "primary key"):
            Permission.objects.bulk_update(self.permissions, ["id"])

    def test_an_object_never_saved(self):
        unsaved = Permission(name="new", codename="new", content_type=self.content_type)
        with self.assertRaisesMessage(ValueError, "must have a primary key"):
            Permission.objects.bulk_update([unsaved], ["name"])

    def test_a_related_object_never_saved(self):
        permission = self.permissions[0]
        permission.content_type = ContentType(app_label="nowhere", model="nothing")
        with self.assertRaisesMessage(ValueError, "bulk_update() prohibited"):
            Permission.objects.bulk_update([permission], ["content_type"])

    def test_a_sliced_queryset(self):
        self.permissions[0].name = "renamed"
        self.permissions[1].name = "renamed"
        first = Permission.objects.order_by("pk")[:1]
        with self.assertRaisesMessage(TypeError, "slice"), transaction.atomic():
            first.bulk_update(self.permissions[:2], ["name"])
        self.assertFalse(Permission.objects.filter(name="renamed").exists())


class DjangoFormTests(PermissionsTestCase):
    """What a table of values cannot express, left to Django's own form and
    sent in batches sized here. The limit is lowered on SQLite, so a hundred
    rows stand in for the thousands it takes in production."""

    def _shout_with_expressions(self):
        for permission in self.permissions:
            permission.name = Upper("name")
            permission.codename = Upper("codename")
            permission.content_type_id = F("content_type_id")

    def _shouted(self):
        ours = Permission.objects.filter(pk__in=[p.pk for p in self.permissions])
        return sum(
            name.startswith("P") and codename.startswith("C")
            for name, codename in ours.values_list("name", "codename")
        )

    def _renamed_with_one_listed_twice(self):
        """Plain values Django's form has to carry: an object comes twice."""
        for permission in self.permissions:
            permission.name = f"renamed {permission.name}"
            permission.codename = f"renamed_{permission.codename}"
        return [*self.permissions, self.permissions[0]]

    def test_a_value_that_is_an_expression_is_computed_by_the_database(self):
        self._shout_with_expressions()
        updated = Permission.objects.bulk_update(self.permissions, FIELDS)
        self.assertEqual(updated, 100)
        self.assertEqual(self._shouted(), 100)

    def test_a_filtered_queryset_only_writes_its_own_rows(self):
        self._renamed_with_one_listed_twice()
        mine = Permission.objects.filter(
            pk__in=[permission.pk for permission in self.permissions[:10]]
        )
        updated = mine.bulk_update(self.permissions, FIELDS)
        self.assertEqual(updated, 10)
        self.assertEqual(self._renamed(), 10)

    def test_a_filtered_queryset_keeps_its_filter_in_the_update(self):
        """Looked up ahead of the write, the rows that match could stop
        matching before it lands."""
        self._renamed_with_one_listed_twice()
        mine = Permission.objects.filter(name__in=["p0", "p1"])
        with CaptureQueriesContext(connection) as queries:
            updated = mine.bulk_update(self.permissions, FIELDS)
        self.assertEqual(updated, 2)
        self.assertEqual(self._renamed(), 2)
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0]["sql"].startswith("UPDATE"))

    def test_an_object_listed_twice_is_written(self):
        objs = self._renamed_with_one_listed_twice()
        Permission.objects.bulk_update(objs, FIELDS)
        self.assertEqual(self._renamed(), 100)

    def test_a_call_naming_no_batch_size_gets_the_default(self):
        self._shout_with_expressions()
        with mock.patch(DEFAULT_BATCH, 25), self.assertNumQueries(4):
            Permission.objects.bulk_update(self.permissions, FIELDS)
        self.assertEqual(self._shouted(), 100)

    def test_a_batch_size_of_the_caller_wider_than_the_default_is_kept(self):
        self._shout_with_expressions()
        with mock.patch(DEFAULT_BATCH, 25), self.assertNumQueries(2):
            Permission.objects.bulk_update(self.permissions, FIELDS, batch_size=50)
        self.assertEqual(self._shouted(), 100)

    def test_more_objects_than_django_sizes_a_batch_for(self):
        objs = self._renamed_with_one_listed_twice()
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            Permission.objects.bulk_update(objs, FIELDS)
        self.assertEqual(self._renamed(), 100)

    def test_a_batch_size_of_the_caller_past_the_limit_is_capped(self):
        objs = self._renamed_with_one_listed_twice()
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            Permission.objects.bulk_update(objs, FIELDS, batch_size=1000)
        self.assertEqual(self._renamed(), 100)

    @ON_SQLITE
    def test_django_still_sizes_its_batches_too_wide(self):
        """The reason widest_batch exists. When this fails, Django counts its
        parameters right: drop widest_batch and keep the default batch."""
        django_bulk_update = QuerySet.bulk_update.__wrapped__
        objs = self._renamed_with_one_listed_twice()
        with (
            sqlite_parameter_limit(PARAMETER_LIMIT),
            self.assertRaises(OperationalError),
            transaction.atomic(),
        ):
            django_bulk_update(Permission.objects.all(), objs, FIELDS)

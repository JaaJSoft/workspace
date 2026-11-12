from types import SimpleNamespace
from unittest import skipUnless

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import OperationalError, connection, transaction
from django.db.models import QuerySet
from django.test import SimpleTestCase, TestCase

from workspace.common.bulk_update_batches import widest_batch
from workspace.common.tests.parameter_limit import sqlite_parameter_limit

PARAMETER_LIMIT = 60
# Three, so that Django's own sizing overruns whatever the limit divides by.
FIELDS = ["name", "codename", "content_type"]
ON_SQLITE = skipUnless(connection.vendor == "sqlite", "the parameter limit is SQLite's")


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


class BulkUpdateWithinTheLimitTests(TestCase):
    """More objects than one statement carries, the limit lowered on SQLite so
    a hundred rows stand in for the thousands it takes in production."""

    @classmethod
    def setUpTestData(cls):
        content_type = ContentType.objects.get_for_model(Group)
        cls.permissions = Permission.objects.bulk_create(
            Permission(name=f"p{i}", codename=f"c{i}", content_type=content_type)
            for i in range(100)
        )

    def _rename(self):
        for permission in self.permissions:
            permission.name = f"renamed {permission.name}"
            permission.codename = f"renamed_{permission.codename}"

    def _renamed(self):
        return Permission.objects.filter(
            name__startswith="renamed ", codename__startswith="renamed_"
        ).count()

    def test_more_objects_than_django_sizes_a_batch_for(self):
        self._rename()
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            updated = Permission.objects.bulk_update(self.permissions, FIELDS)
        self.assertEqual(updated, 100)
        self.assertEqual(self._renamed(), 100)

    def test_fields_given_as_any_iterable(self):
        self._rename()
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            Permission.objects.bulk_update(self.permissions, (name for name in FIELDS))
        self.assertEqual(self._renamed(), 100)

    def test_a_smaller_batch_size_of_the_caller_is_kept(self):
        self._rename()
        with self.assertNumQueries(10):
            Permission.objects.bulk_update(self.permissions, FIELDS, batch_size=10)
        self.assertEqual(self._renamed(), 100)

    def test_a_larger_batch_size_of_the_caller_is_capped(self):
        self._rename()
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            Permission.objects.bulk_update(self.permissions, FIELDS, batch_size=1000)
        self.assertEqual(self._renamed(), 100)

    def test_an_invalid_batch_size_is_still_refused(self):
        for batch_size in (0, -1):
            with self.subTest(batch_size=batch_size), self.assertRaises(ValueError):
                Permission.objects.bulk_update(
                    self.permissions, ["name"], batch_size=batch_size
                )

    @ON_SQLITE
    def test_django_still_sizes_its_batches_too_wide(self):
        """The reason the cap exists. When this fails, Django counts its
        parameters right: delete workspace/common/bulk_update_batches.py."""
        django_bulk_update = QuerySet.bulk_update.__wrapped__
        self._rename()
        with (
            sqlite_parameter_limit(PARAMETER_LIMIT),
            self.assertRaises(OperationalError),
            transaction.atomic(),
        ):
            django_bulk_update(Permission.objects.all(), self.permissions, FIELDS)

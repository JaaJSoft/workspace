from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework import serializers

from workspace.common.serializers import (
    ApiModelSerializer,
    InstantField,
    SummaryField,
    UuidListSerializer,
)

TOKYO = ZoneInfo("Asia/Tokyo")
NOON_UTC = datetime(2026, 12, 5, 12, 0, tzinfo=UTC)


class InstantFieldTests(SimpleTestCase):
    def test_an_instant_is_written_in_utc_whatever_zone_is_active(self):
        """East of UTC, DRF's own field writes the same instant as 21:00+09:00."""
        with timezone.override(TOKYO):
            self.assertEqual(
                InstantField().to_representation(NOON_UTC), "2026-12-05T12:00:00Z"
            )
            self.assertEqual(
                serializers.DateTimeField().to_representation(NOON_UTC),
                "2026-12-05T21:00:00+09:00",
            )

    def test_a_value_carrying_another_offset_is_converted(self):
        self.assertEqual(
            InstantField().to_representation(NOON_UTC.astimezone(TOKYO)),
            "2026-12-05T12:00:00Z",
        )

    def test_a_value_sent_without_an_offset_is_read_in_the_callers_zone(self):
        with timezone.override(TOKYO):
            read = InstantField().to_internal_value("2026-12-05T21:00:00")
        self.assertEqual(read, NOON_UTC)


class ApiModelSerializerTests(SimpleTestCase):
    def test_datetime_columns_are_instants(self):
        class _Serializer(ApiModelSerializer):
            class Meta:
                model = LogEntry
                fields = ["action_time", "object_repr"]

        fields = _Serializer().fields
        self.assertIsInstance(fields["action_time"], InstantField)
        self.assertNotIsInstance(fields["object_repr"], InstantField)


class _TypeSummary(serializers.Serializer):
    id = serializers.IntegerField()
    model = serializers.CharField()


class _PermissionSummary(serializers.Serializer):
    id = serializers.IntegerField()
    codename = serializers.CharField()


def _auth_only(queryset, context):
    return queryset.filter(app_label=context.get("app", "auth"))


class _PermissionSerializer(serializers.ModelSerializer):
    content_type = SummaryField(
        _TypeSummary, queryset=ContentType.objects.all(), narrow=_auth_only
    )

    class Meta:
        model = Permission
        fields = ["name", "codename", "content_type"]


class _GroupSerializer(serializers.ModelSerializer):
    permissions = SummaryField(
        _PermissionSummary,
        queryset=Permission.objects.all(),
        narrow=lambda queryset, context: queryset.filter(codename__endswith="_group"),
        many=True,
    )

    class Meta:
        model = Group
        fields = ["name", "permissions"]


class SummaryFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_type = ContentType.objects.get_for_model(Group)
        cls.foreign_type = ContentType.objects.get_for_model(ContentType)
        cls.permission = Permission.objects.get(codename="add_group")

    def test_a_relation_reads_as_its_small_object(self):
        data = _PermissionSerializer(self.permission).data
        self.assertEqual(
            data["content_type"], {"id": self.group_type.pk, "model": "group"}
        )

    def test_a_relation_is_written_by_its_identifier(self):
        serializer = _PermissionSerializer(
            self.permission, data={"content_type": self.group_type.pk}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["content_type"], self.group_type)

    def test_a_row_outside_what_the_caller_may_name_answers_like_an_unknown_one(self):
        def refusal(pk):
            serializer = _PermissionSerializer(
                self.permission, data={"content_type": pk}, partial=True
            )
            self.assertFalse(serializer.is_valid())
            return str(serializer.errors["content_type"][0]).replace(str(pk), "<pk>")

        self.assertEqual(refusal(self.foreign_type.pk), refusal(987654))

    def test_what_may_be_named_follows_the_context(self):
        serializer = _PermissionSerializer(
            self.permission,
            data={"content_type": self.foreign_type.pk},
            partial=True,
            context={"app": "contenttypes"},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_to_many_relation_reads_and_writes_lists(self):
        group = Group.objects.create(name="editors")
        group.permissions.add(self.permission)
        self.assertEqual(
            _GroupSerializer(group).data["permissions"],
            [{"id": self.permission.pk, "codename": "add_group"}],
        )
        other = Permission.objects.get(codename="change_group")
        writing = _GroupSerializer(
            group, data={"permissions": [other.pk]}, partial=True
        )
        self.assertTrue(writing.is_valid(), writing.errors)
        self.assertEqual(writing.validated_data["permissions"], [other])
        outside = Permission.objects.get(codename="add_permission")
        refused = _GroupSerializer(
            group, data={"permissions": [outside.pk]}, partial=True
        )
        self.assertFalse(refused.is_valid())

    def test_a_writable_relation_must_say_what_may_be_named(self):
        with self.assertRaises(AssertionError):
            SummaryField(_TypeSummary, queryset=ContentType.objects.all())
        SummaryField(_TypeSummary, read_only=True)

    def test_the_choices_of_a_form_are_keyed_by_identifier(self):
        field = _PermissionSerializer().fields["content_type"]
        self.assertIn(str(self.group_type.pk), field.get_choices())
        self.assertNotIn(str(self.foreign_type.pk), field.get_choices())


class UuidListSerializerTests(SimpleTestCase):
    def _errors(self, uuids):
        serializer = UuidListSerializer(data={"uuids": uuids})
        serializer.is_valid()
        return serializer.errors

    def test_a_list_of_distinct_uuids_is_accepted(self):
        uuids = ["0193a5b0-0000-7000-8000-000000000001"]
        self.assertEqual(self._errors(uuids), {})

    def test_an_empty_repeated_or_oversized_list_is_refused(self):
        one = "0193a5b0-0000-7000-8000-000000000001"
        for uuids in ([], [one, one], ["nope"], [one] * 1001, "x"):
            with self.subTest(uuids=str(uuids)[:40]):
                self.assertEqual(list(self._errors(uuids)), ["uuids"])

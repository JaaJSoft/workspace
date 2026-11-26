from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.contrib.admin.models import LogEntry
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import serializers

from workspace.common.serializers import ApiModelSerializer, InstantField

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

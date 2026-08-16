from datetime import UTC, datetime, timedelta

from django.test import TestCase
from django.utils import timezone as dj_timezone

from workspace.common.dates import time_ago


class TimeAgoTests(TestCase):
    NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def tearDown(self):
        dj_timezone.deactivate()

    def _ago(self, **delta):
        return time_ago(self.NOW - timedelta(**delta), now=self.NOW)

    def test_empty_value_yields_empty_string(self):
        self.assertEqual(time_ago(None, now=self.NOW), "")

    def test_under_a_minute_is_just_now(self):
        self.assertEqual(self._ago(seconds=0), "just now")
        self.assertEqual(self._ago(seconds=59), "just now")

    def test_future_timestamp_is_just_now(self):
        self.assertEqual(
            time_ago(self.NOW + timedelta(hours=2), now=self.NOW), "just now"
        )

    def test_minutes(self):
        self.assertEqual(self._ago(seconds=60), "1m ago")
        self.assertEqual(self._ago(minutes=5), "5m ago")
        self.assertEqual(self._ago(seconds=3599), "59m ago")

    def test_hours(self):
        self.assertEqual(self._ago(hours=1), "1h ago")
        self.assertEqual(self._ago(hours=23, minutes=59), "23h ago")

    def test_days(self):
        self.assertEqual(self._ago(days=1), "1d ago")
        self.assertEqual(self._ago(days=6, hours=23), "6d ago")

    def test_rounds_down_to_the_largest_whole_unit(self):
        self.assertEqual(self._ago(seconds=90), "1m ago")
        self.assertEqual(self._ago(minutes=90), "1h ago")
        self.assertEqual(self._ago(hours=36), "1d ago")

    def test_same_year_falls_back_to_month_day(self):
        self.assertEqual(
            time_ago(datetime(2026, 2, 1, 12, 0, tzinfo=UTC), now=self.NOW), "Feb 01"
        )

    def test_other_year_includes_the_year(self):
        self.assertEqual(
            time_ago(datetime(2025, 2, 1, 12, 0, tzinfo=UTC), now=self.NOW),
            "Feb 01, 2025",
        )

    def test_month_label_uses_active_timezone(self):
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        dj_timezone.activate("Europe/Paris")
        self.assertEqual(
            time_ago(datetime(2026, 1, 31, 23, 30, tzinfo=UTC), now=self.NOW), "Feb 01"
        )

    def test_year_boundary_uses_active_timezone(self):
        # 23:30 UTC on Dec 31 2025 is already Jan 1 2026 in Paris - same year
        # as "now", so no year suffix.
        dj_timezone.activate("Europe/Paris")
        self.assertEqual(
            time_ago(datetime(2025, 12, 31, 23, 30, tzinfo=UTC), now=self.NOW), "Jan 01"
        )

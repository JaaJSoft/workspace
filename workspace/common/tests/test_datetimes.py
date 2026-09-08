from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone as dj_timezone

from workspace.common.datetimes import local_date_range, parse_local_datetime

PARIS = ZoneInfo("Europe/Paris")


class ParseLocalDatetimeTests(SimpleTestCase):
    def test_naive_string_is_anchored_in_the_given_zone(self):
        parsed = parse_local_datetime("2026-07-05T14:00", PARIS)
        self.assertEqual(parsed.tzinfo, PARIS)
        # Paris is UTC+2 in July: the same instant is 12:00 UTC.
        self.assertEqual(parsed.astimezone(UTC).hour, 12)

    def test_explicit_offset_wins_over_the_given_zone(self):
        parsed = parse_local_datetime("2026-07-05T14:00:00+00:00", PARIS)
        self.assertEqual(parsed, datetime(2026, 7, 5, 14, tzinfo=UTC))

    def test_unparseable_string_returns_none(self):
        self.assertIsNone(parse_local_datetime("next tuesday", PARIS))
        self.assertIsNone(parse_local_datetime("", PARIS))


class LocalDateRangeTests(SimpleTestCase):
    def test_bounds_are_local_midnights_end_exclusive(self):
        with dj_timezone.override(PARIS):
            start, end = local_date_range(date(2026, 7, 5), date(2026, 7, 6))
        # Paris is UTC+2 in July: local midnight is 22:00 UTC the day before.
        self.assertEqual(start, datetime(2026, 7, 4, 22, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 7, 6, 22, tzinfo=UTC))

    def test_single_day_spans_exactly_one_day(self):
        with dj_timezone.override(UTC):
            start, end = local_date_range(date(2026, 3, 1), date(2026, 3, 1))
        self.assertEqual(start, datetime(2026, 3, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 3, 2, tzinfo=UTC))

    def test_explicit_timezone_overrides_the_active_one(self):
        with dj_timezone.override(UTC):
            start, _ = local_date_range(date(2026, 7, 5), date(2026, 7, 5), tz=PARIS)
        self.assertEqual(start, datetime(2026, 7, 4, 22, tzinfo=UTC))

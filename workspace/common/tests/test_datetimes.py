from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone as dj_timezone

from workspace.common.datetimes import (
    format_clock,
    local_date_range,
    parse_local_datetime,
)

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

    def test_trailing_zone_name_echoed_from_tool_output_is_honoured(self):
        # The goal tools print "2026-10-04 00:30 (Europe/Paris)"; models echo it back.
        parsed = parse_local_datetime("2026-10-04 00:30 (Europe/Paris)", UTC)
        self.assertEqual(parsed, datetime(2026, 10, 3, 22, 30, tzinfo=UTC))

    def test_unknown_trailing_zone_name_returns_none(self):
        self.assertIsNone(
            parse_local_datetime("2026-10-04 00:30 (Mars/Olympus)", PARIS)
        )

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


class FormatClockTests(SimpleTestCase):
    def test_24h_keeps_the_leading_zero(self):
        self.assertEqual(format_clock(time(9, 5), "24h"), "09:05")
        self.assertEqual(format_clock(time(0, 0), "24h"), "00:00")
        self.assertEqual(format_clock(time(23, 59), "24h"), "23:59")

    def test_12h_drops_it_and_names_the_half_of_the_day(self):
        self.assertEqual(format_clock(time(9, 5), "12h"), "9:05 AM")
        self.assertEqual(format_clock(time(0, 30), "12h"), "12:30 AM")
        self.assertEqual(format_clock(time(12, 0), "12h"), "12:00 PM")
        self.assertEqual(format_clock(time(19, 23), "12h"), "7:23 PM")

    def test_an_unknown_format_reads_as_the_default(self):
        self.assertEqual(format_clock(datetime(2026, 1, 1, 14, 5), "36h"), "14:05")

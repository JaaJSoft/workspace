from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from workspace.common.datetimes import parse_local_datetime

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

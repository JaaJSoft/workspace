from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.timezones import event_timezone, normalize_all_day

User = get_user_model()


class NormalizeAllDayTests(TestCase):
    def test_truncates_to_utc_midnight(self):
        dt = datetime(2026, 8, 1, 14, 30, 12, tzinfo=UTC)
        self.assertEqual(normalize_all_day(dt), datetime(2026, 8, 1, tzinfo=UTC))

    def test_uses_the_utc_date_of_aware_values(self):
        # 23:30 in Paris (21:30Z) still belongs to Aug 1 in UTC terms.
        dt = datetime(2026, 8, 1, 23, 30, tzinfo=ZoneInfo("Europe/Paris"))
        self.assertEqual(normalize_all_day(dt), datetime(2026, 8, 1, tzinfo=UTC))

    def test_none_passthrough(self):
        self.assertIsNone(normalize_all_day(None))


class EventTimezoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tz", password="p")
        self.cal = Calendar.objects.create(name="W", owner=self.user)

    def _event(self, tz=""):
        return Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="e",
            start=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            timezone=tz,
        )

    def test_empty_means_none(self):
        self.assertIsNone(event_timezone(self._event()))

    def test_valid_zone(self):
        tz = event_timezone(self._event("Europe/Paris"))
        self.assertEqual(str(tz), "Europe/Paris")

    def test_invalid_zone_falls_back_to_none(self):
        self.assertIsNone(event_timezone(self._event("Not/AZone")))

    def test_explicit_utc_means_none(self):
        # UTC expansion is the legacy fast path; treat it as "no zone".
        self.assertIsNone(event_timezone(self._event("UTC")))

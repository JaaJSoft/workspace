"""Timezone semantics of the ICS import/export pipeline."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import icalendar
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.ics_builder import build_reply
from workspace.calendar.services.ics_common import parse_dt_prop
from workspace.calendar.services.ics_sync import _vevent_to_defaults
from workspace.calendar.services.recurrence_rule import derive_into_defaults

User = get_user_model()

PARIS = ZoneInfo("Europe/Paris")


def _vevent(body):
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\n"
        "BEGIN:VEVENT\r\nUID:x@test\r\nSUMMARY:Test\r\n"
        f"{body}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    cal = icalendar.Calendar.from_ical(ics)
    return next(c for c in cal.walk() if c.name == "VEVENT")


class ParseDtPropTests(TestCase):
    def test_tzid_datetime_converts_to_utc_and_reports_zone(self):
        vevent = _vevent("DTSTART;TZID=Europe/Paris:20260805T090000")
        dt, tzid = parse_dt_prop(vevent.get("DTSTART"))
        self.assertEqual(dt, datetime(2026, 8, 5, 7, 0, tzinfo=UTC))
        self.assertEqual(tzid, "Europe/Paris")

    def test_utc_datetime_has_no_tzid(self):
        vevent = _vevent("DTSTART:20260805T090000Z")
        dt, tzid = parse_dt_prop(vevent.get("DTSTART"))
        self.assertEqual(dt, datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
        self.assertEqual(tzid, "")

    def test_floating_datetime_uses_default_zone(self):
        vevent = _vevent("DTSTART:20260805T090000")
        dt, tzid = parse_dt_prop(vevent.get("DTSTART"), PARIS)
        self.assertEqual(dt, datetime(2026, 8, 5, 7, 0, tzinfo=UTC))
        self.assertEqual(tzid, "Europe/Paris")

    def test_floating_datetime_without_default_falls_back_to_utc(self):
        vevent = _vevent("DTSTART:20260805T090000")
        dt, tzid = parse_dt_prop(vevent.get("DTSTART"))
        self.assertEqual(dt, datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
        self.assertEqual(tzid, "")

    def test_date_value_is_utc_midnight_day_label(self):
        vevent = _vevent("DTSTART;VALUE=DATE:20260805")
        dt, tzid = parse_dt_prop(vevent.get("DTSTART"), PARIS)
        self.assertEqual(dt, datetime(2026, 8, 5, tzinfo=UTC))
        self.assertEqual(tzid, "")


class VeventDefaultsTimezoneTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="ics", password="p")

    def test_zoned_event_records_timezone(self):
        vevent = _vevent("DTSTART;TZID=Europe/Paris:20260805T090000")
        defaults = _vevent_to_defaults(vevent, self.owner, PARIS)
        self.assertEqual(defaults["timezone"], "Europe/Paris")
        self.assertEqual(defaults["start"], datetime(2026, 8, 5, 7, 0, tzinfo=UTC))

    def test_all_day_event_has_no_timezone(self):
        vevent = _vevent("DTSTART;VALUE=DATE:20260805")
        defaults = _vevent_to_defaults(vevent, self.owner, PARIS)
        self.assertTrue(defaults["all_day"])
        self.assertEqual(defaults["timezone"], "")
        self.assertEqual(defaults["start"], datetime(2026, 8, 5, tzinfo=UTC))

    def test_count_end_is_exact_across_dst(self):
        # Daily 09:00 Paris from Mar 27, five occurrences: the last one is
        # Mar 31 at 07:00Z (summer time), not 08:00Z as fixed-step
        # arithmetic would produce.
        vevent = _vevent(
            "DTSTART;TZID=Europe/Paris:20260327T090000\r\nRRULE:FREQ=DAILY;COUNT=5"
        )
        defaults = _vevent_to_defaults(vevent, self.owner, PARIS)
        derive_into_defaults(defaults)
        self.assertEqual(
            defaults["recurrence_until"], datetime(2026, 3, 31, 7, 0, tzinfo=UTC)
        )

    def test_monthly_count_skips_short_months_like_the_engine(self):
        # Monthly from Jan 31, three occurrences: dateutil skips February,
        # so the last one is May 31 (relativedelta arithmetic said Mar 31).
        vevent = _vevent("DTSTART:20260131T100000Z\r\nRRULE:FREQ=MONTHLY;COUNT=3")
        defaults = _vevent_to_defaults(vevent, self.owner, None)
        derive_into_defaults(defaults)
        self.assertEqual(
            defaults["recurrence_until"], datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
        )


class BuildReplyTimezoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="reply", password="p", email="reply@test.com"
        )
        self.cal = Calendar.objects.create(name="W", owner=self.user)

    def _event(self, **kwargs):
        base = {
            "calendar": self.cal,
            "owner": self.user,
            "title": "Meet",
            "start": datetime(2026, 8, 5, 7, 0, tzinfo=UTC),
            "ical_uid": "uid@test",
            "external_organizer": "org@test.com",
        }
        base.update(kwargs)
        return Event.objects.create(**base)

    def test_all_day_emits_value_date(self):
        event = self._event(all_day=True, start=datetime(2026, 8, 5, tzinfo=UTC))
        ics = build_reply(event, self.user, "accepted").decode()
        self.assertIn("DTSTART;VALUE=DATE:20260805", ics)

    def test_zoned_event_emits_tzid_wall_clock_and_vtimezone(self):
        event = self._event(timezone="Europe/Paris")
        ics = build_reply(event, self.user, "accepted").decode()
        self.assertIn("DTSTART;TZID=Europe/Paris:20260805T090000", ics)
        self.assertIn("BEGIN:VTIMEZONE", ics)
        self.assertIn("TZID:Europe/Paris", ics)

    def test_legacy_event_stays_utc(self):
        event = self._event()
        ics = build_reply(event, self.user, "accepted").decode()
        self.assertIn("DTSTART:20260805T070000Z", ics)
        self.assertNotIn("BEGIN:VTIMEZONE", ics)

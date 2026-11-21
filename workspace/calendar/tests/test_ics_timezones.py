"""Timezone semantics of the ICS import/export pipeline."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.recurrence import occurrences_in_range
from workspace.calendar.services.calendar_objects import save_object
from workspace.calendar.services.ical_objects import read_calendar
from workspace.calendar.services.ics_builder import build_reply
from workspace.calendar.services.timezones import zone_from_tzid

User = get_user_model()

PARIS = ZoneInfo("Europe/Paris")


def _object(body, default_tz=UTC, header=""):
    ics = (
        f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\n{header}"
        "BEGIN:VEVENT\r\nUID:x@test\r\nSUMMARY:Test\r\n"
        f"{body}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    (obj,) = read_calendar(ics, default_tz).objects
    return obj


class ReadInstantTests(TestCase):
    def _master(self, body, default_tz=UTC, header=""):
        return _object(body, default_tz, header).master

    def test_tzid_datetime_converts_to_utc_and_reports_zone(self):
        event = self._master("DTSTART;TZID=Europe/Paris:20260805T090000")
        self.assertEqual(event.start, datetime(2026, 8, 5, 7, 0, tzinfo=UTC))
        self.assertEqual(event.timezone, "Europe/Paris")

    def test_utc_datetime_has_no_tzid(self):
        event = self._master("DTSTART:20260805T090000Z")
        self.assertEqual(event.start, datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
        self.assertEqual(event.timezone, "")

    def test_floating_datetime_uses_default_zone(self):
        event = self._master("DTSTART:20260805T090000", PARIS)
        self.assertEqual(event.start, datetime(2026, 8, 5, 7, 0, tzinfo=UTC))
        self.assertEqual(event.timezone, "Europe/Paris")

    def test_floating_datetime_without_default_falls_back_to_utc(self):
        event = self._master("DTSTART:20260805T090000")
        self.assertEqual(event.start, datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
        self.assertEqual(event.timezone, "")

    def test_date_value_is_utc_midnight_day_label(self):
        event = self._master("DTSTART;VALUE=DATE:20260805", PARIS)
        self.assertEqual(event.start, datetime(2026, 8, 5, tzinfo=UTC))
        self.assertEqual(event.timezone, "")
        self.assertTrue(event.all_day)

    def test_x_wr_timezone_takes_over_a_floating_time(self):
        event = self._master(
            "DTSTART:20260805T090000",
            UTC,
            header="X-WR-TIMEZONE:America/New_York\r\n",
        )
        self.assertEqual(event.start, datetime(2026, 8, 5, 13, 0, tzinfo=UTC))
        self.assertEqual(event.timezone, "America/New_York")

    def test_an_unknown_x_wr_timezone_is_ignored(self):
        event = self._master(
            "DTSTART:20260805T090000Z", header="X-WR-TIMEZONE:Nowhere/Land\r\n"
        )
        self.assertEqual(event.start, datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
        self.assertEqual(event.timezone, "")


class ZoneFromTzidTests(TestCase):
    def test_an_iana_name(self):
        self.assertEqual(zone_from_tzid("Europe/Paris"), PARIS)

    def test_a_windows_id(self):
        self.assertEqual(zone_from_tzid("Romance Standard Time"), PARIS)

    def test_a_vendor_prefixed_id(self):
        self.assertEqual(zone_from_tzid("/mozilla.org/20050126_1/Europe/Paris"), PARIS)

    def test_a_name_nothing_knows(self):
        self.assertIsNone(zone_from_tzid("Customized Time Zone"))
        self.assertIsNone(zone_from_tzid("Europe"))
        self.assertIsNone(zone_from_tzid(""))


class SavedSeriesBoundTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="ics", password="p")
        self.calendar = Calendar.objects.create(name="Feed", owner=self.owner)

    def _saved(self, body, default_tz=UTC):
        event = Event(calendar=self.calendar, owner=self.owner, ical_uid="x@test")
        save_object(event, _object(body, default_tz))
        return event

    def test_count_end_is_exact_across_dst(self):
        # Daily 09:00 Paris from Mar 27, five occurrences: the last one is
        # Mar 31 at 07:00Z (summer time), not 08:00Z as fixed-step
        # arithmetic would produce.
        event = self._saved(
            "DTSTART;TZID=Europe/Paris:20260327T090000\r\nRRULE:FREQ=DAILY;COUNT=5",
            PARIS,
        )
        self.assertEqual(
            event.recurrence_until, datetime(2026, 3, 31, 7, 0, tzinfo=UTC)
        )

    def test_monthly_count_skips_short_months_like_the_engine(self):
        # Monthly from Jan 31, three occurrences: dateutil skips February,
        # so the last one is May 31 (relativedelta arithmetic said Mar 31).
        event = self._saved("DTSTART:20260131T100000Z\r\nRRULE:FREQ=MONTHLY;COUNT=3")
        self.assertEqual(
            event.recurrence_until, datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
        )

    def test_a_windows_tzid_on_an_exdate_excludes_its_occurrence(self):
        # 09:00 Romance Standard Time is 08:00Z in winter: read as UTC, the
        # EXDATE would miss the occurrence it cancels.
        event = self._saved(
            "DTSTART;TZID=Europe/Paris:20260302T090000\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=3\r\n"
            "EXDATE;TZID=Romance Standard Time:20260309T090000",
            PARIS,
        )
        self.assertEqual(event.recurrence_until, datetime(2026, 3, 16, 8, tzinfo=UTC))
        self.assertEqual(
            list(
                occurrences_in_range(
                    event,
                    datetime(2026, 3, 1, tzinfo=UTC),
                    datetime(2026, 4, 1, tzinfo=UTC),
                )
            ),
            [datetime(2026, 3, 2, 8, tzinfo=UTC), datetime(2026, 3, 16, 8, tzinfo=UTC)],
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

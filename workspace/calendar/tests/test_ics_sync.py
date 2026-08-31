from unittest.mock import patch

import icalendar
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.services.ics_sync import (
    _recurrence_lines,
    clear_sync_errors,
    external_calendars_with_errors,
    queue_external_calendar_syncs,
)

User = get_user_model()


class ExternalCalendarOpsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="feeds", password="pw")
        cls.ok = ExternalCalendar.objects.create(
            calendar=Calendar.objects.create(name="OK", owner=cls.user),
            url="https://feeds.test/ok.ics",
        )
        cls.broken = ExternalCalendar.objects.create(
            calendar=Calendar.objects.create(name="Broken", owner=cls.user),
            url="https://feeds.test/broken.ics",
            last_error="410 Gone",
        )
        cls.inactive = ExternalCalendar.objects.create(
            calendar=Calendar.objects.create(name="Off", owner=cls.user),
            url="https://feeds.test/off.ics",
            is_active=False,
            last_error="410 Gone",
        )

    def test_external_calendars_with_errors_ignores_inactive_feeds(self):
        self.assertQuerySetEqual(external_calendars_with_errors(), [self.broken])

    def test_queue_syncs_dispatches_active_feeds_only(self):
        with patch(
            "workspace.calendar.tasks.sync_external_calendar_task.delay"
        ) as delay:
            count = queue_external_calendar_syncs(ExternalCalendar.objects.all())

        self.assertEqual(count, 2)
        queued = {call.args[0] for call in delay.call_args_list}
        self.assertEqual(queued, {str(self.ok.uuid), str(self.broken.uuid)})

    def test_clear_sync_errors_only_touches_rows_with_an_error(self):
        count = clear_sync_errors(ExternalCalendar.objects.all())

        self.assertEqual(count, 2)
        self.broken.refresh_from_db()
        self.assertEqual(self.broken.last_error, "")


class RecurrenceLinesTests(TestCase):
    """The stored text is what the feed sent, parameters included.

    A parameter is part of an RDATE's value, not decoration: dropping the
    TZID moves the instant by the zone offset and leaves a floating value
    behind, and dropping VALUE=DATE turns a date into something that is no
    longer one.
    """

    def _vevent(self, *lines):
        ics = "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "BEGIN:VEVENT",
                "UID:params@example.com",
                "DTSTART;TZID=America/New_York:20260106T100000",
                *lines,
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        )
        calendar = icalendar.Calendar.from_ical(ics)
        return next(c for c in calendar.walk() if c.name == "VEVENT")

    def test_tzid_parameter_survives_the_import(self):
        vevent = self._vevent(
            "RRULE:FREQ=WEEKLY",
            "RDATE;TZID=America/New_York:20260501T090000",
        )
        self.assertEqual(
            _recurrence_lines(vevent),
            "RRULE:FREQ=WEEKLY\nRDATE;TZID=America/New_York:20260501T090000",
        )

    def test_value_date_parameter_survives_the_import(self):
        vevent = self._vevent("RRULE:FREQ=WEEKLY", "RDATE;VALUE=DATE:20260601")
        self.assertEqual(
            _recurrence_lines(vevent),
            "RRULE:FREQ=WEEKLY\nRDATE;VALUE=DATE:20260601",
        )

    def test_a_parameterless_rdate_gains_no_semicolon(self):
        vevent = self._vevent("RRULE:FREQ=WEEKLY", "RDATE:20260501T090000Z")
        self.assertEqual(
            _recurrence_lines(vevent),
            "RRULE:FREQ=WEEKLY\nRDATE:20260501T090000Z",
        )

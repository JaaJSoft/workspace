from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.services.ics_sync import (
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

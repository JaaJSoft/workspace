from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.calendar.admin import EventAdmin
from workspace.calendar.models import Calendar, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()


class CalendarAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.ext_broken = ExternalCalendar.objects.create(
            calendar=Calendar.objects.create(name="Broken", owner=cls.admin),
            url="https://feeds.test/broken.ics",
            last_error="410 Gone",
        )
        cls.ext_inactive = ExternalCalendar.objects.create(
            calendar=Calendar.objects.create(name="Off", owner=cls.admin),
            url="https://feeds.test/off.ics",
            is_active=False,
        )
        cls.ext_ok = ExternalCalendar.objects.create(
            calendar=Calendar.objects.create(name="OK", owner=cls.admin),
            url="https://feeds.test/ok.ics",
            last_synced_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_external_calendar_change_list_renders_sync_health(self):
        response = self.client.get(
            reverse("admin:calendar_externalcalendar_changelist")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "broken.ics")

    def test_sync_health_filter_partitions_the_change_list(self):
        url = reverse("admin:calendar_externalcalendar_changelist")
        response = self.client.get(url, {"sync": "error"})
        self.assertContains(response, "broken.ics")
        self.assertNotContains(response, "ok.ics")

        response = self.client.get(url, {"sync": "ok"})
        self.assertContains(response, "ok.ics")
        self.assertNotContains(response, "broken.ics")

    def test_sync_now_queues_active_feeds_only(self):
        with patch(
            "workspace.calendar.tasks.sync_external_calendar_task.delay"
        ) as delay:
            response = self.client.post(
                reverse("admin:calendar_externalcalendar_changelist"),
                {
                    "action": "sync_now",
                    "_selected_action": [
                        str(self.ext_broken.uuid),
                        str(self.ext_inactive.uuid),
                    ],
                },
            )
        self.assertEqual(response.status_code, 302)
        delay.assert_called_once_with(str(self.ext_broken.uuid))

    def test_clear_error_blanks_last_error(self):
        self.client.post(
            reverse("admin:calendar_externalcalendar_changelist"),
            {
                "action": "clear_error",
                "_selected_action": [str(self.ext_broken.uuid)],
            },
        )
        self.ext_broken.refresh_from_db()
        self.assertEqual(self.ext_broken.last_error, "")


class EventAdminRecurrenceTests(TestCase):
    """The change form must not be a second writer of the derived columns."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root2", email="root2@example.com", password="pw"
        )
        cls.calendar = Calendar.objects.create(name="Admin", owner=cls.admin)

    def setUp(self):
        self.client.force_login(self.admin)
        self.factory = RequestFactory()
        self.event = Event(
            calendar=self.calendar,
            owner=self.admin,
            title="Weekly",
            start=datetime(2026, 1, 6, 10, tzinfo=UTC),
            end=datetime(2026, 1, 6, 11, tzinfo=UTC),
        )
        apply_rule(self.event, "")
        self.event.save()

    def _admin(self):
        return EventAdmin(Event, site)

    def test_derived_columns_are_not_editable_on_the_change_form(self):
        request = self.factory.get("/")
        request.user = self.admin
        fields = self._admin().get_form(request, self.event)().fields
        self.assertIn("recurrence_rule", fields)
        self.assertNotIn("is_recurring", fields)
        self.assertNotIn("recurrence_until", fields)

    def test_saving_a_rule_from_the_admin_rederives_the_index_columns(self):
        request = self.factory.post("/")
        request.user = self.admin
        self.event.recurrence_rule = "RRULE:FREQ=DAILY;COUNT=3"
        self._admin().save_model(request, self.event, None, True)

        self.event.refresh_from_db()
        self.assertTrue(self.event.is_recurring)
        self.assertEqual(
            self.event.recurrence_until, datetime(2026, 1, 8, 11, tzinfo=UTC)
        )

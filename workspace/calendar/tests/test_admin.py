from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.calendar.models import Calendar
from workspace.calendar.models_external import ExternalCalendar

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

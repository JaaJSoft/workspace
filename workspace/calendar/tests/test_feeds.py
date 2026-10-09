from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event

User = get_user_model()


class CalendarFeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="feeder", password="pw")
        cls.other = User.objects.create_user(username="stranger", password="pw")
        cls.calendar = Calendar.objects.create(name="Team", owner=cls.owner)
        Event.objects.create(
            calendar=cls.calendar,
            owner=cls.owner,
            title="Planning",
            start=datetime(2026, 10, 5, 9, tzinfo=UTC),
            end=datetime(2026, 10, 5, 10, tzinfo=UTC),
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.settings_url = f"/api/v1/calendars/{self.calendar.pk}/feed"

    def _enable(self):
        response = self.api.post(self.settings_url)
        self.assertEqual(response.status_code, 201)
        return response.json()["url"]

    def test_feed_is_off_until_enabled(self):
        self.assertEqual(self.api.get(self.settings_url).json(), {"url": None})

    def test_enabled_feed_serves_the_calendar_to_anyone(self):
        url = self._enable()
        self.assertTrue(url.startswith("http://testserver/calendar/feeds/"))
        self.assertEqual(self.api.get(self.settings_url).json(), {"url": url})

        response = self.client_class().get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode()
        self.assertIn("X-WR-CALNAME:Team", body)
        self.assertIn("SUMMARY:Planning", body)

    def test_unchanged_feed_answers_304(self):
        url = self._enable()
        etag = self.client.get(url)["ETag"]

        self.assertEqual(
            self.client.get(url, headers={"If-None-Match": etag}).status_code, 304
        )

        Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="New",
            start=datetime(2026, 10, 6, 9, tzinfo=UTC),
        )
        self.assertEqual(
            self.client.get(url, headers={"If-None-Match": etag}).status_code, 200
        )

    def test_rotating_revokes_the_old_url(self):
        old = self._enable()
        new = self._enable()
        self.assertNotEqual(old, new)
        self.assertEqual(self.client.get(old).status_code, 404)
        self.assertEqual(self.client.get(new).status_code, 200)

    def test_disabling_revokes_the_url(self):
        url = self._enable()
        self.assertEqual(self.api.delete(self.settings_url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.api.get(self.settings_url).json(), {"url": None})

    def test_unknown_token(self):
        self.assertEqual(self.client.get("/calendar/feeds/nope.ics").status_code, 404)

    def test_only_the_owner_manages_the_feed(self):
        stranger = APIClient()
        stranger.force_authenticate(self.other)
        for method in ("get", "post", "delete"):
            with self.subTest(method=method):
                response = getattr(stranger, method)(self.settings_url)
                self.assertEqual(response.status_code, 404)
        self.calendar.refresh_from_db()
        self.assertIsNone(self.calendar.feed_token)

    def test_anonymous_cannot_manage_the_feed(self):
        response = APIClient().post(self.settings_url)
        self.assertIn(response.status_code, (401, 403))

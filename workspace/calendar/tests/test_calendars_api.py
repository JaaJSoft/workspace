import uuid

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, CalendarSubscription, Event

from .test_calendar import CalendarTestMixin

# ---------- Calendar CRUD ----------


class CalendarListTests(CalendarTestMixin, APITestCase):
    """Tests for GET /api/v1/calendar/calendars"""

    url = "/api/v1/calendar/calendars"

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_owned_calendars(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["owned"]), 1)
        self.assertEqual(resp.data["owned"][0]["name"], "Work")

    def test_list_includes_subscribed_calendars(self):
        CalendarSubscription.objects.create(user=self.member, calendar=self.calendar)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["subscribed"]), 1)
        self.assertEqual(resp.data["subscribed"][0]["name"], "Work")

    def test_does_not_include_unrelated_calendars(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["owned"]), 0)
        self.assertEqual(len(resp.data["subscribed"]), 0)


class CalendarCreateTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/calendar/calendars"""

    url = "/api/v1/calendar/calendars"

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url, {"name": "New"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {"name": "Personal"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["name"], "Personal")
        self.assertEqual(resp.data["owner"]["username"], "owner")

    def test_create_calendar_default_color(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {"name": "Default Color"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["color"], "primary")

    def test_create_calendar_custom_color(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url, {"name": "Custom", "color": "accent"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["color"], "accent")

    def test_create_calendar_missing_name(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class CalendarDetailTests(CalendarTestMixin, APITestCase):
    """Tests for PUT/DELETE /api/v1/calendar/calendars/<id>"""

    def url(self, calendar_id):
        return f"/api/v1/calendar/calendars/{calendar_id}"

    def test_update_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.calendar.uuid),
            {"name": "Renamed", "color": "secondary"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Renamed")
        self.assertEqual(resp.data["color"], "secondary")

    def test_update_calendar_not_owner_returns_404(self):
        self.client.force_authenticate(self.member)
        resp = self.client.put(
            self.url(self.calendar.uuid),
            {"name": "Hacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_nonexistent_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(uuid.uuid4()),
            {"name": "Ghost"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url(self.calendar.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Calendar.objects.filter(uuid=self.calendar.uuid).exists())

    def test_delete_calendar_not_owner_returns_404(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.calendar.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_calendar_cascades_events(self):
        self.client.force_authenticate(self.owner)
        event_id = self.event.uuid
        self.client.delete(self.url(self.calendar.uuid))
        self.assertFalse(Event.objects.filter(uuid=event_id).exists())

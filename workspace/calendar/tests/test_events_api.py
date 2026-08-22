import uuid
from datetime import UTC, datetime, timedelta

from django.core.cache import cache
from django.utils import timezone
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, CalendarSubscription, Event, EventMember
from workspace.calendar.search import search_events
from workspace.users.services.settings import set_setting

from .test_calendar import CalendarTestMixin

# ---------- Event CRUD ----------


class EventListTests(CalendarTestMixin, APITestCase):
    """Tests for GET /api/v1/events"""

    url = "/api/v1/events"

    def _range_params(self, days_before=7, days_after=7):
        start = (timezone.now() - timedelta(days=days_before)).isoformat()
        end = (timezone.now() + timedelta(days=days_after)).isoformat()
        return {"start": start, "end": end}

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url, self._range_params())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_start_and_end_params(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.get(self.url, {"start": timezone.now().isoformat()})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.get(self.url, {"end": timezone.now().isoformat()})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_events_in_range(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e["title"] for e in resp.data]
        self.assertIn("Team Meeting", titles)

    def test_excludes_events_outside_range(self):
        # Create an event far in the future
        Event.objects.create(
            calendar=self.calendar,
            title="Far Future",
            start=timezone.now() + timedelta(days=365),
            end=timezone.now() + timedelta(days=365, hours=1),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("Far Future", titles)

    def test_includes_events_from_subscribed_calendars(self):
        CalendarSubscription.objects.create(user=self.member, calendar=self.calendar)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url, self._range_params())
        titles = [e["title"] for e in resp.data]
        self.assertIn("Team Meeting", titles)

    def test_includes_events_where_user_is_member(self):
        # member is invited to self.event via setUp
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url, self._range_params())
        titles = [e["title"] for e in resp.data]
        self.assertIn("Team Meeting", titles)

    def test_filter_by_calendar_ids(self):
        other_cal = Calendar.objects.create(name="Other", owner=self.owner)
        Event.objects.create(
            calendar=other_cal,
            title="Other Event",
            start=timezone.now() + timedelta(hours=1),
            end=timezone.now() + timedelta(hours=2),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        params = self._range_params()
        params["calendar_ids"] = str(other_cal.uuid)
        resp = self.client.get(self.url, params)
        titles = [e["title"] for e in resp.data]
        self.assertIn("Other Event", titles)
        self.assertNotIn("Team Meeting", titles)

    def test_all_day_event_in_range(self):
        Event.objects.create(
            calendar=self.calendar,
            title="All Day",
            start=timezone.now() + timedelta(days=2),
            end=None,
            all_day=True,
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())
        titles = [e["title"] for e in resp.data]
        self.assertIn("All Day", titles)


class EventCreateTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/events"""

    url = "/api/v1/events"

    def _event_data(self, **overrides):
        data = {
            "calendar_id": str(self.calendar.uuid),
            "title": "New Event",
            "start": (timezone.now() + timedelta(days=2)).isoformat(),
            "end": (timezone.now() + timedelta(days=2, hours=1)).isoformat(),
        }
        data.update(overrides)
        return data

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url, self._event_data(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, self._event_data(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["title"], "New Event")
        self.assertEqual(resp.data["owner"]["username"], "owner")

    def test_create_event_with_members(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(member_ids=[self.member.id])
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        member_usernames = [m["user"]["username"] for m in resp.data["members"]]
        self.assertIn("member", member_usernames)

    def test_create_event_owner_excluded_from_members(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(member_ids=[self.owner.id, self.member.id])
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        member_usernames = [m["user"]["username"] for m in resp.data["members"]]
        self.assertNotIn("owner", member_usernames)
        self.assertIn("member", member_usernames)

    def test_create_event_all_day(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(all_day=True, end=None)
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["all_day"])

    def test_create_event_missing_title(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data()
        del data["title"]
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_event_calendar_not_owned(self):
        outsider_cal = Calendar.objects.create(name="Outsider Cal", owner=self.outsider)
        self.client.force_authenticate(self.owner)
        data = self._event_data(calendar_id=str(outsider_cal.uuid))
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_event_nonexistent_calendar(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(calendar_id=str(uuid.uuid4()))
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class EventDetailTests(CalendarTestMixin, APITestCase):
    """Tests for GET/PUT/DELETE /api/v1/events/<id>"""

    def url(self, event_id):
        return f"/api/v1/events/{event_id}"

    # --- GET ---

    def test_get_event_as_owner(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["title"], "Team Meeting")

    def test_get_event_as_member(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_get_event_as_subscriber(self):
        CalendarSubscription.objects.create(user=self.outsider, calendar=self.calendar)
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_get_event_no_access(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_nonexistent_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- PUT ---

    def test_update_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {"title": "Updated Meeting"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["title"], "Updated Meeting")

    def test_update_event_partial_fields(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {"description": "Added description"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["description"], "Added description")
        # title unchanged
        self.assertEqual(resp.data["title"], "Team Meeting")

    def test_update_event_change_calendar(self):
        new_cal = Calendar.objects.create(name="Personal", owner=self.owner)
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {"calendar_id": str(new_cal.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["calendar_id"], str(new_cal.uuid))

    def test_update_event_add_members(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {"member_ids": [self.member.id, self.outsider.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        member_usernames = [m["user"]["username"] for m in resp.data["members"]]
        self.assertIn("outsider", member_usernames)
        self.assertIn("member", member_usernames)

    def test_update_event_remove_members(self):
        self.client.force_authenticate(self.owner)
        # Remove all members
        resp = self.client.put(
            self.url(self.event.uuid),
            {"member_ids": []},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["members"]), 0)

    def test_update_event_not_owner_returns_403(self):
        self.client.force_authenticate(self.member)
        resp = self.client.put(
            self.url(self.event.uuid),
            {"title": "Hacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- DELETE ---

    def test_delete_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Event.objects.filter(uuid=self.event.uuid).exists())

    def test_delete_event_not_owner_returns_403(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ---------- Event Respond ----------


class EventRespondTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/events/<id>/respond"""

    def url(self, event_id):
        return f"/api/v1/events/{event_id}/respond"

    def test_unauthenticated_rejected(self):
        resp = self.client.post(
            self.url(self.event.uuid),
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_accept_invitation(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid),
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "accepted")
        membership = EventMember.objects.get(event=self.event, user=self.member)
        self.assertEqual(membership.status, EventMember.Status.ACCEPTED)

    def test_decline_invitation(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid),
            {"status": "declined"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "declined")

    def test_not_invited_returns_403(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            self.url(self.event.uuid),
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_status_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid),
            {"status": "maybe"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------- Search ----------


class SearchTests(CalendarTestMixin, APITestCase):
    """Tests for search_events() function."""

    def test_search_finds_owned_calendar_events(self):
        results = search_events("Team", self.owner, limit=10)
        names = [r.name for r in results]
        self.assertIn("Team Meeting", names)

    def test_search_finds_subscribed_calendar_events(self):
        CalendarSubscription.objects.create(user=self.outsider, calendar=self.calendar)
        results = search_events("Team", self.outsider, limit=10)
        names = [r.name for r in results]
        self.assertIn("Team Meeting", names)

    def test_search_finds_events_where_member(self):
        results = search_events("Team", self.member, limit=10)
        names = [r.name for r in results]
        self.assertIn("Team Meeting", names)

    def test_search_excludes_inaccessible_events(self):
        results = search_events("Team", self.outsider, limit=10)
        self.assertEqual(len(results), 0)

    def test_search_filters_by_title(self):
        Event.objects.create(
            calendar=self.calendar,
            title="Lunch Break",
            start=timezone.now() + timedelta(hours=3),
            owner=self.owner,
        )
        results = search_events("Lunch", self.owner, limit=10)
        names = [r.name for r in results]
        self.assertIn("Lunch Break", names)
        self.assertNotIn("Team Meeting", names)

    def test_search_respects_limit(self):
        for i in range(5):
            Event.objects.create(
                calendar=self.calendar,
                title=f"Event {i}",
                start=timezone.now() + timedelta(hours=i),
                owner=self.owner,
            )
        results = search_events("Event", self.owner, limit=3)
        self.assertEqual(len(results), 3)


class AllDayApiContractTests(CalendarTestMixin, APITestCase):
    """All-day events: normalized UTC-midnight storage, date-only API shape,
    and timezone stamping for timed events."""

    url = "/api/v1/events"

    def tearDown(self):
        dj_timezone.deactivate()
        cache.clear()

    def test_all_day_create_normalizes_and_serializes_date_only(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url,
            {
                "calendar_id": str(self.calendar.uuid),
                "title": "Summit",
                "start": "2026-08-05T14:30:00Z",
                "all_day": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        event = Event.objects.get(title="Summit")
        self.assertEqual(event.start, datetime(2026, 8, 5, tzinfo=UTC))
        self.assertEqual(event.timezone, "")
        self.assertEqual(resp.data["start"], "2026-08-05")

    def test_all_day_create_accepts_date_only_strings(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url,
            {
                "calendar_id": str(self.calendar.uuid),
                "title": "Trip",
                "start": "2026-08-05",
                "end": "2026-08-07",
                "all_day": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        event = Event.objects.get(title="Trip")
        self.assertEqual(event.start, datetime(2026, 8, 5, tzinfo=UTC))
        self.assertEqual(event.end, datetime(2026, 8, 7, tzinfo=UTC))
        self.assertEqual(resp.data["start"], "2026-08-05")
        self.assertEqual(resp.data["end"], "2026-08-07")

    def test_timed_create_stamps_active_timezone(self):
        set_setting(self.owner, "core", "timezone", "Europe/Paris")
        self.client.force_login(self.owner)
        resp = self.client.post(
            self.url,
            {
                "calendar_id": str(self.calendar.uuid),
                "title": "Standup",
                "start": "2026-08-05T09:00:00+02:00",
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        event = Event.objects.get(title="Standup")
        self.assertEqual(event.timezone, "Europe/Paris")
        self.assertEqual(event.start, datetime(2026, 8, 5, 7, 0, tzinfo=UTC))

    def test_all_day_update_keeps_invariant(self):
        event = Event.objects.create(
            calendar=self.calendar,
            title="Trip",
            start=datetime(2026, 8, 5, tzinfo=UTC),
            all_day=True,
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"start": "2026-08-06T10:15:00Z"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.start, datetime(2026, 8, 6, tzinfo=UTC))

    def test_recurring_all_day_occurrences_are_date_only(self):
        Event.objects.create(
            calendar=self.calendar,
            title="Daily standdown",
            start=datetime(2026, 8, 3, tzinfo=UTC),
            all_day=True,
            owner=self.owner,
            recurrence_frequency="daily",
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(
            self.url,
            {"start": "2026-08-03T00:00:00Z", "end": "2026-08-06T00:00:00Z"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        starts = [e["start"] for e in resp.data if e["title"] == "Daily standdown"]
        self.assertEqual(starts, ["2026-08-03", "2026-08-04", "2026-08-05"])


class RangeEndpointTimezoneTests(CalendarTestMixin, APITestCase):
    url = "/api/v1/events"

    def tearDown(self):
        dj_timezone.deactivate()
        cache.clear()

    def test_all_day_stays_date_only_for_negative_offset_user(self):
        # Rendering must not shift the day label when the active timezone
        # has a negative offset (the rendered ISO string carries -07:00).
        Event.objects.create(
            calendar=self.calendar,
            title="Label day",
            start=datetime(2026, 8, 5, tzinfo=UTC),
            all_day=True,
            owner=self.owner,
        )
        set_setting(self.owner, "core", "timezone", "America/Los_Angeles")
        self.client.force_login(self.owner)
        resp = self.client.get(
            self.url,
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-10T00:00:00Z"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event = next(e for e in resp.data if e["title"] == "Label day")
        self.assertEqual(event["start"], "2026-08-05")

    def test_invalid_range_returns_400(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, {"start": "garbage", "end": "2026-08-10"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_naive_range_is_interpreted_in_active_timezone(self):
        # 2026-08-05T23:30 Paris is 21:30Z; a naive end of 2026-08-05T23:00
        # (Paris) must exclude it, while the same naive end in UTC would not.
        Event.objects.create(
            calendar=self.calendar,
            title="Late event",
            start=datetime(2026, 8, 5, 21, 30, tzinfo=UTC),
            end=datetime(2026, 8, 5, 22, 0, tzinfo=UTC),
            owner=self.owner,
        )
        set_setting(self.owner, "core", "timezone", "Europe/Paris")
        self.client.force_login(self.owner)
        resp = self.client.get(
            self.url, {"start": "2026-08-05T00:00", "end": "2026-08-05T23:00"}
        )
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("Late event", titles)
        resp = self.client.get(
            self.url, {"start": "2026-08-05T00:00", "end": "2026-08-06T00:00"}
        )
        titles = [e["title"] for e in resp.data]
        self.assertIn("Late event", titles)

    def test_mixed_all_day_and_timed_sorting(self):
        Event.objects.create(
            calendar=self.calendar,
            title="Day label",
            start=datetime(2026, 8, 5, tzinfo=UTC),
            all_day=True,
            owner=self.owner,
        )
        Event.objects.create(
            calendar=self.calendar,
            title="Morning meeting",
            start=datetime(2026, 8, 5, 7, 0, tzinfo=UTC),
            end=datetime(2026, 8, 5, 8, 0, tzinfo=UTC),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(
            self.url,
            {"start": "2026-08-04T00:00:00Z", "end": "2026-08-07T00:00:00Z"},
        )
        titles = [
            e["title"]
            for e in resp.data
            if e["title"] in ("Day label", "Morning meeting")
        ]
        self.assertEqual(titles, ["Day label", "Morning meeting"])


class TimezoneStampingScopeTests(CalendarTestMixin, APITestCase):
    """Only a series GAINING recurrence adopts the editor's zone; legacy
    recurring series keep UTC expansion whatever else is edited."""

    url = "/api/v1/events"

    def tearDown(self):
        dj_timezone.deactivate()
        cache.clear()

    def _login_paris(self):
        set_setting(self.owner, "core", "timezone", "Europe/Paris")
        self.client.force_login(self.owner)

    def test_editing_legacy_recurring_series_keeps_utc_expansion(self):
        event = Event.objects.create(
            calendar=self.calendar,
            title="Legacy daily",
            start=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            owner=self.owner,
            recurrence_frequency="daily",
        )
        self._login_paris()
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"title": "Legacy daily renamed"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.timezone, "")

    def test_gaining_recurrence_stamps_active_timezone(self):
        event = Event.objects.create(
            calendar=self.calendar,
            title="One-off",
            start=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            owner=self.owner,
        )
        self._login_paris()
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"recurrence_frequency": "daily"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.recurrence_frequency, "daily")
        self.assertEqual(event.timezone, "Europe/Paris")

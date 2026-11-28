import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, CalendarSubscription, Event, EventMember
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.search import search_events
from workspace.calendar.services import recurrence_rule
from workspace.calendar.services.recurrence_rule import apply_rule
from workspace.users.services.settings import set_setting

from .test_calendar import CalendarTestMixin

User = get_user_model()

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

    def test_a_date_that_cannot_be_read_is_a_400_naming_it(self):
        self.client.force_authenticate(self.owner)
        for name in ("start", "end"):
            for value in ("9" * 30, "tomorrow"):
                with self.subTest(name=name, value=value):
                    resp = self.client.get(self.url, {name: value})
                    self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertEqual(list(resp.data), [name])

    def test_without_a_start_the_list_begins_now(self):
        past = Event.objects.create(
            calendar=self.calendar,
            title="Yesterday",
            start=timezone.now() - timedelta(days=1),
            end=timezone.now() - timedelta(days=1) + timedelta(hours=1),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e["title"] for e in resp.data]
        self.assertIn("Team Meeting", titles)
        self.assertNotIn(past.title, titles)

    def test_the_list_is_paged(self):
        day = timezone.now() + timedelta(days=3)
        for hour in range(3):
            Event.objects.create(
                calendar=self.calendar,
                title=f"Slot {hour}",
                start=day + timedelta(hours=hour),
                owner=self.owner,
            )
        self.client.force_authenticate(self.owner)
        params = {
            "start": day.isoformat(),
            "end": (day + timedelta(days=1)).isoformat(),
            "limit": 2,
        }
        first = self.client.get(self.url, {**params, "count": "true"})
        self.assertEqual([e["title"] for e in first.data], ["Slot 0", "Slot 1"])
        self.assertEqual(first["X-Has-More"], "true")
        self.assertEqual(first["X-Total-Count"], "3")
        second = self.client.get(self.url, {**params, "offset": 2})
        self.assertEqual([e["title"] for e in second.data], ["Slot 2"])
        self.assertEqual(second["X-Has-More"], "false")

    def test_a_list_with_no_end_cannot_be_counted(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, {"count": "true"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["count"])

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

    def test_filter_by_calendar(self):
        other_cal = Calendar.objects.create(name="Other", owner=self.owner)
        Event.objects.create(
            calendar=other_cal,
            title="Other Event",
            start=timezone.now() + timedelta(hours=1),
            end=timezone.now() + timedelta(hours=2),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        for params in (
            {"calendar": str(other_cal.uuid)},
            {"calendar_name": "other"},
        ):
            with self.subTest(params=params):
                resp = self.client.get(self.url, {**self._range_params(), **params})
                titles = [e["title"] for e in resp.data]
                self.assertEqual(titles, ["Other Event"])
        both = self.client.get(
            self.url,
            {
                **self._range_params(),
                "calendar": f"{other_cal.uuid},{self.calendar.uuid}",
            },
        )
        self.assertEqual(
            sorted(e["title"] for e in both.data), ["Other Event", "Team Meeting"]
        )

    def test_a_calendar_the_user_cannot_see_lists_nothing(self):
        """Not even the event of it they are invited to: asking for a
        calendar out of reach answers like asking for one that is not there,
        by its uuid and by its name alike."""
        Event.objects.create(
            calendar=self.calendar,
            title="Private Appointment",
            start=timezone.now() + timedelta(hours=1),
            end=timezone.now() + timedelta(hours=2),
            owner=self.owner,
        )
        self.client.force_authenticate(self.member)
        for params in (
            {"calendar": str(self.calendar.uuid)},
            {"calendar": str(uuid.uuid4())},
            {"calendar_name": self.calendar.name},
            {"calendar_name": "no such calendar"},
        ):
            with self.subTest(params=params):
                resp = self.client.get(self.url, {**self._range_params(), **params})
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                self.assertEqual(resp.data, [])

    def test_calendar_none_lists_the_events_one_is_only_invited_to(self):
        own = Calendar.objects.create(name="Mine", owner=self.member)
        Event.objects.create(
            calendar=own,
            title="My Own",
            start=timezone.now() + timedelta(hours=1),
            owner=self.member,
        )
        self.client.force_authenticate(self.member)
        invited = self.client.get(
            self.url, {**self._range_params(), "calendar": "none"}
        )
        self.assertEqual([e["title"] for e in invited.data], ["Team Meeting"])
        # The calendar the organizer filed it in is not the invitee's to see.
        self.assertIsNone(invited.data[0]["calendar"])
        both = self.client.get(
            self.url, {**self._range_params(), "calendar": f"{own.uuid},none"}
        )
        self.assertEqual(
            sorted(e["title"] for e in both.data), ["My Own", "Team Meeting"]
        )

    def test_malformed_calendar_is_rejected(self):
        self.client.force_authenticate(self.owner)
        params = self._range_params()
        params["calendar"] = f"{self.calendar.uuid},not-a-uuid"
        resp = self.client.get(self.url, params)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["calendar"])

    def test_declined_events_are_left_out_unless_asked_for(self):
        EventMember.objects.filter(event=self.event, user=self.member).update(
            status=EventMember.Status.DECLINED
        )
        self.client.force_authenticate(self.member)
        hidden = self.client.get(self.url, self._range_params())
        self.assertEqual(hidden.data, [])
        shown = self.client.get(
            self.url, {**self._range_params(), "include_declined": "true"}
        )
        self.assertEqual([e["title"] for e in shown.data], ["Team Meeting"])
        refused = self.client.get(
            self.url, {**self._range_params(), "include_declined": "maybe"}
        )
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)

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
            "calendar": str(self.calendar.uuid),
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
        data = self._event_data(members=[self.member.id])
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        member_usernames = [m["user"]["username"] for m in resp.data["members"]]
        self.assertIn("member", member_usernames)

    def test_create_event_owner_excluded_from_members(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(members=[self.owner.id, self.member.id])
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        member_usernames = [m["user"]["username"] for m in resp.data["members"]]
        self.assertNotIn("owner", member_usernames)
        self.assertIn("member", member_usernames)

    def test_create_event_all_day(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(is_all_day=True, end=None)
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["is_all_day"])

    def test_create_event_missing_title(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data()
        del data["title"]
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_event_calendar_not_owned(self):
        outsider_cal = Calendar.objects.create(name="Outsider Cal", owner=self.outsider)
        self.client.force_authenticate(self.owner)
        data = self._event_data(calendar=str(outsider_cal.uuid))
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_event_nonexistent_calendar(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(calendar=str(uuid.uuid4()))
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_calendar_out_of_reach_answers_like_one_that_does_not_exist(self):
        """Someone else's calendar, and one the caller only subscribed to."""
        outsider_cal = Calendar.objects.create(name="Outsider Cal", owner=self.outsider)
        subscribed = Calendar.objects.create(name="Subscribed", owner=self.outsider)
        CalendarSubscription.objects.create(user=self.owner, calendar=subscribed)
        self.client.force_authenticate(self.owner)

        def refusal(calendar_uuid):
            resp = self.client.post(
                self.url, self._event_data(calendar=str(calendar_uuid)), format="json"
            )
            return resp.status_code, resp.content.replace(
                str(calendar_uuid).encode(), b"<uuid>"
            )

        absent = refusal(uuid.uuid4())
        self.assertEqual(absent[0], status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refusal(outsider_cal.uuid), absent)
        self.assertEqual(refusal(subscribed.uuid), absent)
        self.assertFalse(Event.objects.filter(title="New Event").exists())

    def test_create_event_in_an_external_calendar_is_refused(self):
        external = Calendar.objects.create(name="Feed", owner=self.owner)
        ExternalCalendar.objects.create(
            calendar=external, url="https://example.com/feed.ics"
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url, self._event_data(calendar=str(external.uuid)), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("calendar", resp.data)
        self.assertFalse(Event.objects.filter(calendar=external).exists())


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

    def test_get_event_no_access_looks_like_a_missing_event(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.event.uuid))
        missing = self.client.get(self.url(uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.data, missing.data)

    def test_outsider_write_looks_like_a_missing_event(self):
        self.client.force_authenticate(self.outsider)
        patch = self.client.patch(
            self.url(self.event.uuid), {"title": "Hacked"}, format="json"
        )
        delete = self.client.delete(self.url(self.event.uuid))
        missing = self.client.patch(
            self.url(uuid.uuid4()), {"title": "Hacked"}, format="json"
        )
        self.assertEqual(patch.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(patch.content, missing.content)
        self.assertEqual(delete.status_code, status.HTTP_404_NOT_FOUND)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Team Meeting")
        self.assertFalse(self.event.is_cancelled)

    def test_get_nonexistent_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- PUT ---

    def test_update_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            self.url(self.event.uuid),
            {"title": "Updated Meeting"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["title"], "Updated Meeting")

    def test_update_event_partial_fields(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
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
        resp = self.client.patch(
            self.url(self.event.uuid),
            {"calendar": str(new_cal.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["calendar"]["uuid"], str(new_cal.uuid))

    def test_update_event_add_members(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            self.url(self.event.uuid),
            {"members": [self.member.id, self.outsider.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        member_usernames = [m["user"]["username"] for m in resp.data["members"]]
        self.assertIn("outsider", member_usernames)
        self.assertIn("member", member_usernames)

    def test_update_event_remove_members(self):
        self.client.force_authenticate(self.owner)
        # Remove all members
        resp = self.client.patch(
            self.url(self.event.uuid),
            {"members": []},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["members"]), 0)

    def test_update_event_not_owner_returns_403(self):
        self.client.force_authenticate(self.member)
        resp = self.client.patch(
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


class EventResponseTests(CalendarTestMixin, APITestCase):
    """PUT and DELETE /api/v1/events/<uuid>/response"""

    def url(self, event_id):
        return f"/api/v1/events/{event_id}/response"

    def _status(self, user=None, event=None):
        return EventMember.objects.get(
            event=event or self.event, user=user or self.member
        ).status

    def test_unauthenticated_rejected(self):
        resp = self.client.put(
            self.url(self.event.uuid), {"status": "accepted"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_accept_then_decline(self):
        self.client.force_authenticate(self.member)
        for answer in ("accepted", "declined"):
            with self.subTest(answer=answer):
                resp = self.client.put(
                    self.url(self.event.uuid), {"status": answer}, format="json"
                )
                self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
                self.assertEqual(resp.content, b"")
                self.assertEqual(self._status(), answer)

    def test_answering_twice_changes_nothing_more(self):
        self.client.force_authenticate(self.member)
        for _ in range(2):
            resp = self.client.put(
                self.url(self.event.uuid), {"status": "accepted"}, format="json"
            )
            self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._status(), EventMember.Status.ACCEPTED)

    def test_taking_an_answer_back_leaves_the_invitation_pending(self):
        EventMember.objects.filter(event=self.event, user=self.member).update(
            status=EventMember.Status.DECLINED
        )
        self.client.force_authenticate(self.member)
        for _ in range(2):
            resp = self.client.delete(self.url(self.event.uuid))
            self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._status(), EventMember.Status.PENDING)

    def test_someone_who_sees_the_event_without_being_invited_is_refused(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid), {"status": "accepted"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_event_out_of_reach_answers_like_one_that_does_not_exist(self):
        self.client.force_authenticate(self.outsider)
        hidden = self.client.put(
            self.url(self.event.uuid), {"status": "accepted"}, format="json"
        )
        absent = self.client.put(
            self.url(uuid.uuid4()), {"status": "accepted"}, format="json"
        )
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_invalid_status_rejected(self):
        self.client.force_authenticate(self.member)
        for answer in ("maybe", "pending"):
            with self.subTest(answer=answer):
                resp = self.client.put(
                    self.url(self.event.uuid), {"status": answer}, format="json"
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._status(), EventMember.Status.PENDING)

    def test_an_occurrence_changed_on_its_own_takes_its_own_answer(self):
        """Its invitees are its own rows: an answer given for its slot lands
        on them, and the series keeps the answer it had."""
        start = timezone.now() + timedelta(days=1)
        series = Event(
            calendar=self.calendar,
            title="Weekly",
            start=start,
            end=start + timedelta(hours=1),
            owner=self.owner,
        )
        apply_rule(series, "RRULE:FREQ=WEEKLY")
        series.save()
        EventMember.objects.create(event=series, user=self.member)
        slot = start + timedelta(weeks=1)
        changed = Event.objects.create(
            calendar=self.calendar,
            title="Weekly, moved",
            start=slot + timedelta(hours=2),
            owner=self.owner,
            recurrence_parent=series,
            original_start=slot,
        )
        EventMember.objects.create(event=changed, user=self.member)
        self.client.force_authenticate(self.member)

        resp = self.client.put(
            self.url(series.uuid),
            {"status": "declined"},
            format="json",
            query_params={"original_start": slot.isoformat()},
        )

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._status(event=changed), EventMember.Status.DECLINED)
        self.assertEqual(self._status(event=series), EventMember.Status.PENDING)

        # Another slot of the series is the series itself.
        resp = self.client.put(
            self.url(series.uuid),
            {"status": "accepted"},
            format="json",
            query_params={"original_start": (slot + timedelta(weeks=1)).isoformat()},
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._status(event=series), EventMember.Status.ACCEPTED)
        self.assertEqual(self._status(event=changed), EventMember.Status.DECLINED)


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
                "calendar": str(self.calendar.uuid),
                "title": "Summit",
                "start": "2026-08-05T14:30:00Z",
                "is_all_day": True,
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
                "calendar": str(self.calendar.uuid),
                "title": "Trip",
                "start": "2026-08-05",
                "end": "2026-08-07",
                "is_all_day": True,
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
                "calendar": str(self.calendar.uuid),
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
        resp = self.client.patch(
            f"{self.url}/{event.uuid}",
            {"start": "2026-08-06T10:15:00Z"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.start, datetime(2026, 8, 6, tzinfo=UTC))

    def test_recurring_all_day_occurrences_are_date_only(self):
        from workspace.calendar.services.recurrence_rule import apply_rule

        event = Event(
            calendar=self.calendar,
            title="Daily standdown",
            start=datetime(2026, 8, 3, tzinfo=UTC),
            all_day=True,
            owner=self.owner,
        )
        apply_rule(event, "RRULE:FREQ=DAILY")
        event.save()
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
        event = Event(
            calendar=self.calendar,
            title="Legacy daily",
            start=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            owner=self.owner,
        )
        apply_rule(event, "RRULE:FREQ=DAILY")
        event.save()
        self._login_paris()
        resp = self.client.patch(
            f"{self.url}/{event.uuid}",
            {"title": "Legacy daily renamed"},
            content_type="application/json",
            query_params={"occurrences": "all"},
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
        resp = self.client.patch(
            f"{self.url}/{event.uuid}",
            {"recurrence_rule": "RRULE:FREQ=DAILY"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertTrue(event.is_recurring)
        self.assertEqual(event.timezone, "Europe/Paris")


class RecurrenceRuleApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(self.user)
        self.cal = Calendar.objects.create(name="Test", owner=self.user)

    def _create(self, rule):
        return self.client.post(
            "/api/v1/events",
            {
                "calendar": str(self.cal.uuid),
                "title": "E",
                "start": "2026-01-06T10:00:00Z",
                "end": "2026-01-06T11:00:00Z",
                "recurrence_rule": rule,
            },
            format="json",
        )

    def test_complex_rule_survives_a_round_trip_byte_for_byte(self):
        rule = "RRULE:FREQ=MONTHLY;BYDAY=2TU"
        response = self._create(rule)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["recurrence_rule"], rule)

    def test_complex_rule_reports_itself_as_not_simple(self):
        # recurrence_simple is null, which is what puts the web picker into
        # read-only mode instead of letting it overwrite the rule.
        response = self._create("RRULE:FREQ=MONTHLY;BYDAY=2TU")
        self.assertIsNone(response.json()["recurrence_simple"])

    def test_simple_rule_exposes_picker_fields_and_a_summary(self):
        response = self._create("RRULE:FREQ=DAILY;INTERVAL=3")
        body = response.json()
        self.assertEqual(body["recurrence_simple"]["frequency"], "daily")
        self.assertEqual(body["recurrence_simple"]["interval"], 3)
        self.assertEqual(body["recurrence_summary"], "Every 3 days")

    def test_recurring_occurrence_from_range_endpoint_exposes_summary_and_simple(self):
        # The event modal reads a recurring occurrence's recurrence state off
        # the answer of its series with other times, not a row serialized
        # on its own - it must carry the same derived fields.
        self._create("RRULE:FREQ=DAILY;INTERVAL=2")
        resp = self.client.get(
            "/api/v1/events",
            {"start": "2026-01-06T00:00:00Z", "end": "2026-01-10T00:00:00Z"},
        )
        self.assertEqual(resp.status_code, 200)
        occurrences = [e for e in resp.json() if e["title"] == "E"]
        self.assertEqual(len(occurrences), 2)
        for occ in occurrences:
            self.assertTrue(occ["is_recurring"])
            self.assertEqual(occ["recurrence_summary"], "Every 2 days")
            self.assertEqual(occ["recurrence_simple"]["frequency"], "daily")
            self.assertEqual(occ["recurrence_simple"]["interval"], 2)

    def _create_exception(self, master, days_after_start):
        exc_start = master.start + timedelta(days=days_after_start)
        return Event.objects.create(
            calendar=self.cal,
            title="E (moved)",
            start=exc_start + timedelta(hours=1),
            end=exc_start + timedelta(hours=2),
            owner=self.user,
            recurrence_parent=master,
            original_start=exc_start,
        )

    def test_materialized_exception_exposes_master_summary_and_simple(self):
        # A materialized exception is a *different* Event row than its
        # master, fetched by a separate query - the derived fields must
        # still reflect the master's rule, not the exception's own (blank)
        # recurrence_rule.
        create_resp = self._create("RRULE:FREQ=DAILY;INTERVAL=2")
        master = Event.objects.get(uuid=create_resp.json()["uuid"])
        self._create_exception(master, days_after_start=2)

        resp = self.client.get(
            "/api/v1/events",
            {"start": "2026-01-06T00:00:00Z", "end": "2026-01-10T00:00:00Z"},
        )
        self.assertEqual(resp.status_code, 200)
        occ = next(e for e in resp.json() if e["title"] == "E (moved)")
        self.assertTrue(occ["is_exception"])
        self.assertEqual(occ["recurrence_summary"], "Every 2 days")
        self.assertEqual(occ["recurrence_simple"]["frequency"], "daily")
        self.assertEqual(occ["recurrence_simple"]["interval"], 2)

    def test_recurrence_summary_computed_once_per_master_not_per_occurrence(self):
        # A series that yields both an occurrence of the rule and one
        # changed on its own must call describe() exactly once, not once
        # per occurrence: the changed one is another row, serialized on its
        # own, and still reads the rule its series already described.
        create_resp = self._create("RRULE:FREQ=DAILY;INTERVAL=2")
        master = Event.objects.get(uuid=create_resp.json()["uuid"])
        self._create_exception(master, days_after_start=2)

        with patch.object(
            recurrence_rule, "describe", wraps=recurrence_rule.describe
        ) as mock_describe:
            resp = self.client.get(
                "/api/v1/events",
                {"start": "2026-01-06T00:00:00Z", "end": "2026-01-10T00:00:00Z"},
            )
        self.assertEqual(resp.status_code, 200)
        occurrences = [e for e in resp.json() if e["title"] in ("E", "E (moved)")]
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(mock_describe.call_count, 1)


class RecurrenceRuleValidationTests(APITestCase):
    """Unparseable rule text must be refused at the boundary.

    Stored, it becomes a master with no bound: no window query prunes it, so
    it is loaded, expanded and logged on every calendar read of every window.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="strict", password="pass")
        self.client.force_authenticate(self.user)
        self.cal = Calendar.objects.create(name="Test", owner=self.user)

    def _create(self, rule):
        return self.client.post(
            "/api/v1/events",
            {
                "calendar": str(self.cal.uuid),
                "title": "E",
                "start": "2026-01-06T10:00:00Z",
                "end": "2026-01-06T11:00:00Z",
                "recurrence_rule": rule,
            },
            format="json",
        )

    def test_create_rejects_an_unparseable_rule(self):
        resp = self._create("total garbage")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("recurrence_rule", resp.json())
        self.assertFalse(Event.objects.filter(title="E").exists())

    def test_update_rejects_an_unparseable_rule(self):
        created = self._create("RRULE:FREQ=DAILY;COUNT=3")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        resp = self.client.patch(
            f"/api/v1/events/{created.json()['uuid']}",
            {"recurrence_rule": "RRULE:FREQ=NONSENSE"},
            format="json",
            query_params={"occurrences": "all"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.json()), ["recurrence_rule"])

    def test_a_blank_rule_still_clears_the_series(self):
        created = self._create("RRULE:FREQ=DAILY;COUNT=3")
        event_uuid = created.json()["uuid"]
        resp = self.client.patch(
            f"/api/v1/events/{event_uuid}",
            {"recurrence_rule": ""},
            format="json",
            query_params={"occurrences": "all"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Event.objects.get(uuid=event_uuid).is_recurring)

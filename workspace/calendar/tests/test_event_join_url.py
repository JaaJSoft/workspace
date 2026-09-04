from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event

User = get_user_model()


class EventJoinUrlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", "owner@example.com", "pw")
        self.calendar = Calendar.objects.create(owner=self.user, name="Cal")
        self.event = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Sync",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
        )
        self.client.force_login(self.user)

    def test_join_url_is_null_without_a_meeting(self):
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["join_url"])

    def test_join_url_is_absolute_once_a_meeting_exists(self):
        from workspace.chat.services.meetings import create_meeting

        meeting = create_meeting(self.event, self.user)
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(
            resp.json()["join_url"], f"http://testserver/meet/{meeting.slug}"
        )

    def test_listing_events_does_not_query_per_event_for_the_meeting(self):
        from workspace.chat.services.meetings import create_meeting

        create_meeting(self.event, self.user)
        for i in range(5):
            Event.objects.create(
                calendar=self.calendar,
                owner=self.user,
                title=f"E{i}",
                start=timezone.now() + timezone.timedelta(hours=3 + i),
                end=timezone.now() + timezone.timedelta(hours=4 + i),
            )
        start = (timezone.now() - timezone.timedelta(days=1)).isoformat()
        end = (timezone.now() + timezone.timedelta(days=1)).isoformat()
        with self.assertNumQueries(self._list_query_count(start, end)):
            self.client.get(f"/api/v1/events?start={start}&end={end}")

    def _list_query_count(self, start, end):
        # Measured once against the listing with a single event; the count
        # must not grow with the number of events (the meeting is
        # select_related).
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(f"/api/v1/events?start={start}&end={end}")
        return len(ctx.captured_queries)

    def test_recurring_occurrences_carry_the_absolute_join_url(self):
        from workspace.chat.services.meetings import create_meeting

        master = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Standup",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
            recurrence_frequency="daily",
        )
        meeting = create_meeting(master, self.user)
        start = timezone.now().isoformat()
        end = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        resp = self.client.get(f"/api/v1/events?start={start}&end={end}")
        occurrences = [e for e in resp.json() if e["title"] == "Standup"]
        self.assertGreaterEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertEqual(occ["join_url"], f"http://testserver/meet/{meeting.slug}")

    def test_recurring_occurrences_are_null_without_a_meeting(self):
        Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Standup",
            start=timezone.now() + timezone.timedelta(hours=1),
            end=timezone.now() + timezone.timedelta(hours=2),
            recurrence_frequency="daily",
        )
        start = timezone.now().isoformat()
        end = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        resp = self.client.get(f"/api/v1/events?start={start}&end={end}")
        occurrences = [e for e in resp.json() if e["title"] == "Standup"]
        self.assertGreaterEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertIsNone(occ["join_url"])

    def test_upcoming_listing_carries_the_absolute_join_url(self):
        from workspace.chat.services.meetings import create_meeting

        meeting = create_meeting(self.event, self.user)
        after = timezone.now().isoformat()
        resp = self.client.get(f"/api/v1/events?after={after}&limit=20")
        events = resp.json()["events"]
        matching = [e for e in events if e["uuid"] == str(self.event.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0]["join_url"], f"http://testserver/meet/{meeting.slug}"
        )

    def test_upcoming_listing_does_not_query_per_event_for_the_meeting(self):
        from workspace.chat.services.meetings import create_meeting

        create_meeting(self.event, self.user)
        for i in range(5):
            Event.objects.create(
                calendar=self.calendar,
                owner=self.user,
                title=f"E{i}",
                start=timezone.now() + timezone.timedelta(hours=3 + i),
                end=timezone.now() + timezone.timedelta(hours=4 + i),
            )
        after = timezone.now().isoformat()
        with self.assertNumQueries(self._upcoming_query_count(after)):
            self.client.get(f"/api/v1/events?after={after}&limit=20")

    def _upcoming_query_count(self, after):
        # Same measuring pattern as _list_query_count: the count must not
        # grow with the number of events.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(f"/api/v1/events?after={after}&limit=20")
        return len(ctx.captured_queries)

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

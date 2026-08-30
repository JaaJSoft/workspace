from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()


class FinalOccurrenceOverlapTests(APITestCase):
    """The master prune and the expansion must agree about the last occurrence.

    The old prune compared a window against the last occurrence's START, while
    the expansion widened its floor by the event's duration. A series whose
    final occurrence straddles the start of the window fell into that gap and
    vanished from the view.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(self.user)
        self.cal = Calendar.objects.create(name="Test", owner=self.user)
        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Long meeting",
            start=datetime(2026, 1, 5, 10, tzinfo=UTC),
            end=datetime(2026, 1, 5, 12, tzinfo=UTC),
        )
        apply_rule(event, "RRULE:FREQ=DAILY;UNTIL=20260107T100000Z")
        event.save()

    def test_final_occurrence_shows_in_a_window_it_straddles(self):
        response = self.client.get(
            "/api/v1/events",
            {"start": "2026-01-07T11:00:00Z", "end": "2026-01-07T23:00:00Z"},
        )
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.json()]
        self.assertIn("Long meeting", titles)

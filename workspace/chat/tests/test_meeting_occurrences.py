from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import Conversation, Meeting
from workspace.chat.services.meeting_occurrences import current_occurrence


class OccurrenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="occ", password="x")
        self.cal = Calendar.objects.create(name="C", owner=self.user)

    def _meeting(self, **event_kwargs):
        event = Event.objects.create(
            calendar=self.cal, owner=self.user, title="E", **event_kwargs
        )
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        return Meeting.objects.create(
            event=event, conversation=conv, created_by=self.user
        )

    def test_single_event_in_progress(self):
        now = timezone.now()
        m = self._meeting(
            start=now - timedelta(minutes=5), end=now + timedelta(minutes=25)
        )
        occ = current_occurrence(m, now=now)
        self.assertIsNotNone(occ)
        self.assertEqual(occ[0], m.event.start)

    def test_open_during_the_lobby_lead(self):
        now = timezone.now()
        m = self._meeting(
            start=now + timedelta(minutes=10), end=now + timedelta(minutes=40)
        )
        self.assertIsNotNone(current_occurrence(m, now=now))

    def test_closed_before_the_lobby_lead(self):
        now = timezone.now()
        m = self._meeting(start=now + timedelta(hours=3), end=now + timedelta(hours=4))
        self.assertIsNone(current_occurrence(m, now=now))

    def test_open_during_the_grace_period(self):
        now = timezone.now()
        m = self._meeting(
            start=now - timedelta(minutes=70), end=now - timedelta(minutes=10)
        )
        self.assertIsNotNone(current_occurrence(m, now=now))

    def test_closed_after_the_grace_period(self):
        now = timezone.now()
        m = self._meeting(start=now - timedelta(hours=5), end=now - timedelta(hours=4))
        self.assertIsNone(current_occurrence(m, now=now))

    def test_event_without_end_uses_the_default_duration(self):
        now = timezone.now()
        m = self._meeting(start=now - timedelta(minutes=30), end=None)
        occ = current_occurrence(m, now=now)
        self.assertIsNotNone(occ)
        self.assertEqual(occ[1], m.event.start + timedelta(minutes=60))

    def test_recurring_event_opens_for_a_later_occurrence(self):
        now = timezone.now()
        # Weekly series that started three weeks ago; today's instance is live.
        m = self._meeting(
            start=now - timedelta(weeks=3, minutes=5),
            end=now - timedelta(weeks=3) + timedelta(minutes=25),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        occ = current_occurrence(m, now=now)
        self.assertIsNotNone(occ)
        # It is this week's occurrence, not the original series start.
        self.assertGreater(occ[0], m.event.start)

    def test_recurring_event_is_closed_between_occurrences(self):
        now = timezone.now()
        m = self._meeting(
            start=now - timedelta(days=3, hours=2),
            end=now - timedelta(days=3, hours=1),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        self.assertIsNone(current_occurrence(m, now=now))

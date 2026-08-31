from datetime import timedelta

from django.conf import settings
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
        # current_occurrence truncates to the same microsecond grain rrule
        # uses for recurring series, so this is the value a caller must write
        # back into occurrence_start/closed_occurrence_start, not event.start.
        self.assertEqual(occ[0], m.event.start.replace(microsecond=0))

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
        self.assertEqual(
            occ[1], m.event.start.replace(microsecond=0) + timedelta(minutes=60)
        )

    def test_open_exactly_at_the_lobby_open_boundary(self):
        now = timezone.now().replace(microsecond=0)
        start = now + settings.MEETING_LOBBY_LEAD
        m = self._meeting(start=start, end=start + timedelta(minutes=30))
        occ = current_occurrence(m, now=now)
        self.assertIsNotNone(occ)
        self.assertEqual(occ[0], start)

    def test_open_exactly_at_the_grace_close_boundary(self):
        now = timezone.now().replace(microsecond=0)
        end = now - settings.MEETING_GRACE
        start = end - timedelta(minutes=30)
        m = self._meeting(start=start, end=end)
        occ = current_occurrence(m, now=now)
        self.assertIsNotNone(occ)
        self.assertEqual(occ[1], end)

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
        # Exactly this week's instance, three intervals after the series
        # start - not merely "later than the series start".
        expected_start = m.event.start.replace(microsecond=0) + timedelta(weeks=3)
        self.assertEqual(occ[0], expected_start)

    def test_recurring_event_is_closed_between_occurrences(self):
        now = timezone.now()
        m = self._meeting(
            start=now - timedelta(days=3, hours=2),
            end=now - timedelta(days=3, hours=1),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        self.assertIsNone(current_occurrence(m, now=now))

    def test_cancelled_occurrence_is_not_reachable(self):
        now = timezone.now()
        m = self._meeting(
            start=now - timedelta(weeks=3, minutes=5),
            end=now - timedelta(weeks=3) + timedelta(minutes=25),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        occurrence_start = m.event.start.replace(microsecond=0) + timedelta(weeks=3)
        Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="E (cancelled)",
            start=occurrence_start,
            end=occurrence_start + timedelta(minutes=30),
            recurrence_parent=m.event,
            original_start=occurrence_start,
            is_cancelled=True,
        )
        self.assertIsNone(current_occurrence(m, now=now))

    def test_rescheduled_occurrence_is_reachable_at_its_new_time_only(self):
        now = timezone.now()
        m = self._meeting(
            start=now - timedelta(weeks=3, minutes=5),
            end=now - timedelta(weeks=3) + timedelta(minutes=25),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        original_start = m.event.start.replace(microsecond=0) + timedelta(weeks=3)
        new_start = now + timedelta(hours=2)
        new_end = new_start + timedelta(minutes=30)
        Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="E (rescheduled)",
            start=new_start,
            end=new_end,
            recurrence_parent=m.event,
            original_start=original_start,
        )
        # The ghost slot at the original time is closed.
        self.assertIsNone(current_occurrence(m, now=now))
        # The real, rescheduled slot is open, at its new time.
        self.assertEqual(current_occurrence(m, now=new_start), (new_start, new_end))

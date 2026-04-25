"""Tests for ``get_upcoming_for_user`` — dashboard "today" event widget.

The function should return events that are happening today AND not yet
finished. That covers three buckets:

* events still upcoming today (``start >= now`` AND ``start <= end_of_today``)
* events currently ongoing (``start <= now`` AND ``end >= now``)
* recurring occurrences matching either of the above
"""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.calendar.upcoming import get_upcoming_for_user

User = get_user_model()


class GetUpcomingForUserTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='u', password='pass123')
        self.calendar = Calendar.objects.create(name='Work', owner=self.user)
        self.now = timezone.now().replace(hour=14, minute=0, second=0, microsecond=0)
        self.end_of_today = timezone.make_aware(
            timezone.datetime.combine(self.now.date(), time.max),
            timezone.get_current_timezone(),
        )

    def _make(self, title, start, end=None, **kwargs):
        return Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title=title,
            start=start,
            end=end,
            **kwargs,
        )

    # ── ongoing ────────────────────────────────────────────────────

    def test_ongoing_event_included(self):
        """Started 30min ago, ends in 1h — must appear."""
        self._make(
            'Ongoing meeting',
            start=self.now - timedelta(minutes=30),
            end=self.now + timedelta(hours=1),
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, 'Ongoing meeting')

    def test_multiday_event_spanning_today_included(self):
        """Started yesterday, ends tomorrow — still ongoing today."""
        self._make(
            'Conference',
            start=self.now - timedelta(days=1),
            end=self.now + timedelta(days=1),
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(len(result), 1)

    # ── upcoming today ─────────────────────────────────────────────

    def test_future_event_today_included(self):
        self._make(
            'Later today',
            start=self.now + timedelta(hours=2),
            end=self.now + timedelta(hours=3),
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(len(result), 1)

    def test_event_without_end_in_future_included(self):
        self._make('Reminder', start=self.now + timedelta(hours=1), end=None)
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(len(result), 1)

    # ── excluded ───────────────────────────────────────────────────

    def test_already_finished_today_excluded(self):
        """Started this morning, finished an hour ago — not relevant anymore."""
        self._make(
            'Done',
            start=self.now - timedelta(hours=3),
            end=self.now - timedelta(hours=1),
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(result, [])

    def test_event_without_end_in_past_excluded(self):
        self._make('Past reminder', start=self.now - timedelta(hours=1), end=None)
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(result, [])

    def test_event_starting_tomorrow_excluded(self):
        self._make(
            'Tomorrow',
            start=self.now + timedelta(days=1),
            end=self.now + timedelta(days=1, hours=1),
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(result, [])

    def test_cancelled_event_excluded(self):
        self._make(
            'Cancelled',
            start=self.now + timedelta(hours=1),
            end=self.now + timedelta(hours=2),
            is_cancelled=True,
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(result, [])

    # ── all-day ────────────────────────────────────────────────────

    def test_all_day_event_today_included(self):
        """All-day event of today (start at 00:00, no end) must show all day."""
        start_of_today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._make('Holiday', start=start_of_today, end=None, all_day=True)
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, 'Holiday')

    def test_all_day_event_yesterday_excluded(self):
        start_of_yesterday = (
            self.now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=1)
        )
        self._make('Yesterday', start=start_of_yesterday, end=None, all_day=True)
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(result, [])

    # ── recurring ──────────────────────────────────────────────────

    def test_recurring_ongoing_occurrence_included(self):
        """Daily event whose today's occurrence is in progress."""
        master_start = self.now.replace(hour=13, minute=30) - timedelta(days=7)
        master_end = master_start + timedelta(hours=1)  # 13:30→14:30 daily
        self._make(
            'Daily standup',
            start=master_start,
            end=master_end,
            recurrence_frequency=Event.RecurrenceFrequency.DAILY,
            recurrence_interval=1,
        )
        result = get_upcoming_for_user(self.user, self.now, self.end_of_today)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, 'Daily standup')

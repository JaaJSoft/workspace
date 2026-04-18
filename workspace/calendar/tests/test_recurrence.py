from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.calendar.recurrence import _build_rrule, make_virtual_occurrence, next_occurrences_after

User = get_user_model()


class BuildRruleTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.cal = Calendar.objects.create(name='Test', owner=self.user)

    def _make_master(self, freq, start, end=None, interval=1, rec_end=None):
        return Event.objects.create(
            calendar=self.cal, owner=self.user, title='Recurring',
            start=start, end=end,
            recurrence_frequency=freq, recurrence_interval=interval,
            recurrence_end=rec_end,
        )

    def test_daily_recurrence(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('daily', start=now)
        range_start = now
        range_end = now + timedelta(days=3)
        occs = list(_build_rrule(master, range_start, range_end))
        self.assertEqual(len(occs), 3)

    def test_weekly_recurrence(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('weekly', start=now)
        range_start = now
        range_end = now + timedelta(weeks=3)
        occs = list(_build_rrule(master, range_start, range_end))
        self.assertEqual(len(occs), 3)

    def test_monthly_recurrence(self):
        now = timezone.now().replace(day=1, hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('monthly', start=now)
        range_start = now
        range_end = now + timedelta(days=90)
        occs = list(_build_rrule(master, range_start, range_end))
        self.assertGreaterEqual(len(occs), 3)

    def test_interval_respected(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('daily', start=now, interval=2)
        range_start = now
        range_end = now + timedelta(days=6)
        occs = list(_build_rrule(master, range_start, range_end))
        self.assertEqual(len(occs), 3)  # day 0, 2, 4

    def test_recurrence_end_respected(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        rec_end = now + timedelta(days=2)
        master = self._make_master('daily', start=now, rec_end=rec_end)
        range_start = now
        range_end = now + timedelta(days=10)
        occs = list(_build_rrule(master, range_start, range_end))
        self.assertLessEqual(len(occs), 3)

    def test_unknown_frequency_yields_nothing(self):
        now = timezone.now()
        master = self._make_master('daily', start=now)
        master.recurrence_frequency = 'bogus'
        occs = list(_build_rrule(master, now, now + timedelta(days=5)))
        self.assertEqual(occs, [])

    def test_event_with_duration_overlaps(self):
        """Events with end time: yield if occurrence end > range_start."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=1)
        master = self._make_master('daily', start=start, end=end)
        range_start = now
        range_end = now + timedelta(days=1)
        occs = list(_build_rrule(master, range_start, range_end))
        # The first occurrence starts 2h before range_start but ends 1h after
        self.assertGreaterEqual(len(occs), 1)

    def test_past_occurrences_excluded(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=10)
        master = self._make_master('daily', start=start)
        range_start = now
        range_end = now + timedelta(days=2)
        occs = list(_build_rrule(master, range_start, range_end))
        for occ in occs:
            self.assertGreaterEqual(occ, range_start)


class MakeVirtualOccurrenceTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.cal = Calendar.objects.create(name='Test', owner=self.user)

    def test_returns_dict_with_expected_keys(self):
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal, owner=self.user, title='Meeting',
            start=now, end=now + timedelta(hours=1),
            recurrence_frequency='daily',
        )
        occ = make_virtual_occurrence(master, now)
        self.assertEqual(occ['title'], 'Meeting')
        self.assertTrue(occ['is_recurring'])
        self.assertFalse(occ['is_exception'])
        self.assertEqual(occ['master_event_id'], str(master.uuid))

    def test_computes_end_from_duration(self):
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal, owner=self.user, title='Meeting',
            start=now, end=now + timedelta(hours=2),
            recurrence_frequency='daily',
        )
        occ_start = now + timedelta(days=1)
        occ = make_virtual_occurrence(master, occ_start)
        expected_end = (occ_start + timedelta(hours=2)).isoformat()
        self.assertEqual(occ['end'], expected_end)

    def test_end_is_none_when_no_master_end(self):
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal, owner=self.user, title='All day',
            start=now, end=None,
            recurrence_frequency='daily',
        )
        occ = make_virtual_occurrence(master, now)
        self.assertIsNone(occ['end'])


class NextOccurrencesAfterTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='bob', password='pass')
        self.cal = Calendar.objects.create(name='Test', owner=self.user)

    def _make_master(self, freq, start, end=None, interval=1, rec_end=None):
        return Event.objects.create(
            calendar=self.cal, owner=self.user, title='Recurring',
            start=start, end=end,
            recurrence_frequency=freq, recurrence_interval=interval,
            recurrence_end=rec_end,
        )

    def test_future_master_yields_limit_occurrences(self):
        """Weekly master starting tomorrow → first 20 occurrences."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('weekly', start=now + timedelta(days=1))
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(len(occs), 20)
        # First is tomorrow, then weekly
        self.assertEqual(occs[0], now + timedelta(days=1))
        self.assertEqual(occs[1], now + timedelta(days=8))

    def test_past_master_skips_past_occurrences(self):
        """Master starting 1 year ago → occurrences are all >= after."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('weekly', start=now - timedelta(days=365))
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(len(occs), 20)
        for occ in occs:
            self.assertGreaterEqual(occ, now)

    def test_limit_respected(self):
        """limit=5 → exactly 5 occurrences."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('daily', start=now)
        occs = list(next_occurrences_after(master, after=now, limit=5))
        self.assertEqual(len(occs), 5)

    def test_recurrence_end_stops_iteration(self):
        """Weekly master with recurrence_end exactly at week 3 → 4 occurrences
        (weeks 0, 1, 2, 3) because dateutil.rrule treats `until` as inclusive."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master(
            'weekly', start=now,
            rec_end=now + timedelta(weeks=3),
        )
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(len(occs), 4)  # weeks 0, 1, 2, 3

    def test_interval_respected(self):
        """Bi-weekly master → 14-day stride."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master('weekly', start=now, interval=2)
        occs = list(next_occurrences_after(master, after=now, limit=3))
        self.assertEqual(len(occs), 3)
        self.assertEqual(occs[1] - occs[0], timedelta(days=14))
        self.assertEqual(occs[2] - occs[1], timedelta(days=14))

    def test_non_recurring_master_yields_nothing(self):
        """Non-recurring Event passed in → generator yields nothing."""
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal, owner=self.user, title='One-off',
            start=now, end=now + timedelta(hours=1),
        )
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(occs, [])

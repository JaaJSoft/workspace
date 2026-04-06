from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.calendar.recurrence import _build_rrule, make_virtual_occurrence

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

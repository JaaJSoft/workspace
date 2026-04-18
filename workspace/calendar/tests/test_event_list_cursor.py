"""Tests for the cursor (agenda) mode of /api/v1/calendar/events.

The endpoint supports two modes:
- Range mode (existing): ?start=&end=&calendar_ids= → flat JSON array.
- Cursor mode (new): ?after=&limit=&calendar_ids=&show_declined= →
    {events: [...], next_after: "<iso>" | null}.
Mode is selected by the presence of the `after` query param.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember

User = get_user_model()


class CursorModeMixin:
    """Common setup for cursor-mode tests."""

    url = '/api/v1/calendar/events'

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', email='owner@test.com', password='pass123',
        )
        self.other = User.objects.create_user(
            username='other', email='other@test.com', password='pass123',
        )
        self.cal = Calendar.objects.create(name='Work', owner=self.owner)
        self.client.force_authenticate(self.owner)

    def _iso(self, dt):
        return dt.isoformat()

    def _get_cursor(self, after, **extra):
        params = {'after': self._iso(after), 'limit': 20, **extra}
        return self.client.get(self.url, params)

    def _make_event(self, title, start, end=None, calendar=None):
        return Event.objects.create(
            calendar=calendar or self.cal,
            owner=self.owner,
            title=title,
            start=start,
            end=end or start + timedelta(hours=1),
        )


class CursorModeEmptyTests(CursorModeMixin, APITestCase):

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(user=None)  # clear auth
        now = timezone.now()
        resp = self._get_cursor(after=now)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_database_returns_empty_page(self):
        now = timezone.now()
        resp = self._get_cursor(after=now)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'events': [], 'next_after': None})

    def test_cursor_mode_selected_when_after_param_present(self):
        """Response shape must be {events, next_after}, not a flat list."""
        now = timezone.now()
        resp = self._get_cursor(after=now)
        self.assertIn('events', resp.data)
        self.assertIn('next_after', resp.data)

    def test_range_mode_still_returns_flat_list(self):
        """Backward-compat: ?start=&end= → flat list."""
        now = timezone.now()
        resp = self.client.get(self.url, {
            'start': self._iso(now),
            'end': self._iso(now + timedelta(days=7)),
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)

    def test_missing_all_params_returns_400(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class CursorModeNonRecurringTests(CursorModeMixin, APITestCase):

    def test_fewer_than_limit_returns_all(self):
        now = timezone.now()
        for i in range(5):
            self._make_event(f'E{i}', now + timedelta(days=i + 1))
        resp = self._get_cursor(after=now)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['events']), 5)
        self.assertIsNone(resp.data['next_after'])

    def test_exact_limit_returns_no_cursor(self):
        now = timezone.now()
        for i in range(20):
            self._make_event(f'E{i}', now + timedelta(days=i + 1))
        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 20)
        self.assertIsNone(resp.data['next_after'])

    def test_pagination(self):
        now = timezone.now()
        for i in range(30):
            self._make_event(f'E{i}', now + timedelta(days=i + 1))

        resp1 = self._get_cursor(after=now)
        self.assertEqual(len(resp1.data['events']), 20)
        self.assertIsNotNone(resp1.data['next_after'])

        resp2 = self.client.get(self.url, {
            'after': resp1.data['next_after'], 'limit': 20,
        })
        self.assertEqual(len(resp2.data['events']), 10)
        self.assertIsNone(resp2.data['next_after'])

    def test_past_events_excluded(self):
        now = timezone.now()
        for i in range(10):
            self._make_event(f'Past{i}', now - timedelta(days=i + 1))
        for i in range(5):
            self._make_event(f'Future{i}', now + timedelta(days=i + 1))
        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 5)
        for e in resp.data['events']:
            self.assertTrue(e['title'].startswith('Future'))

    def test_limit_param_respected(self):
        now = timezone.now()
        for i in range(10):
            self._make_event(f'E{i}', now + timedelta(days=i + 1))
        resp = self.client.get(self.url, {
            'after': self._iso(now), 'limit': 3,
        })
        self.assertEqual(len(resp.data['events']), 3)
        self.assertIsNotNone(resp.data['next_after'])

    def test_limit_param_capped_at_100(self):
        now = timezone.now()
        for i in range(101):
            self._make_event(f'E{i}', now + timedelta(days=i + 1))
        resp = self.client.get(self.url, {
            'after': self._iso(now), 'limit': 500,
        })
        self.assertEqual(len(resp.data['events']), 100)

    def test_events_sorted_by_start_ascending(self):
        now = timezone.now()
        # Create in non-sorted order
        self._make_event('C', now + timedelta(days=3))
        self._make_event('A', now + timedelta(days=1))
        self._make_event('B', now + timedelta(days=2))
        resp = self._get_cursor(after=now)
        titles = [e['title'] for e in resp.data['events']]
        self.assertEqual(titles, ['A', 'B', 'C'])


class CursorModeRecurringTests(CursorModeMixin, APITestCase):
    """Edge cases for recurring event expansion in cursor mode."""

    def _make_weekly(self, start, rec_end=None, title='Weekly'):
        return Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title=title,
            start=start,
            end=start + timedelta(hours=1),
            recurrence_frequency='weekly',
            recurrence_interval=1,
            recurrence_end=rec_end,
        )

    def test_recurring_weekly_future_master(self):
        """Weekly master starting tomorrow → 20 occurrences + cursor for 21st."""
        now = timezone.now().replace(microsecond=0)
        self._make_weekly(start=now + timedelta(days=1))
        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 20)
        self.assertIsNotNone(resp.data['next_after'])

    def test_recurring_master_in_past(self):
        """Master with start 1 year ago, no recurrence_end → 20 FUTURE
        occurrences returned. Master itself not in list; no occ has start < now."""
        now = timezone.now().replace(microsecond=0)
        self._make_weekly(start=now - timedelta(days=365))
        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 20)
        for e in resp.data['events']:
            self.assertGreaterEqual(e['start'], self._iso(now))

    def test_recurring_master_in_past_with_exception(self):
        """Past master + materialized exception next week.
        The exception appears in the list; the virtual occurrence at that
        original_start is skipped (no duplicate)."""
        now = timezone.now().replace(microsecond=0)
        master = self._make_weekly(start=now - timedelta(days=365))

        # Find a future occurrence datetime produced by rrule to pin the exception
        from workspace.calendar.recurrence import next_occurrences_after
        future_occs = list(next_occurrences_after(master, after=now, limit=2))
        target_occ = future_occs[0]

        exception = Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title='Moved meeting',
            start=target_occ + timedelta(hours=2),  # moved 2 hours later
            end=target_occ + timedelta(hours=3),
            recurrence_parent=master,
            original_start=target_occ,
        )

        resp = self._get_cursor(after=now)
        titles = [e['title'] for e in resp.data['events']]
        self.assertIn('Moved meeting', titles)
        # The count is still 20 (exception replaced the virtual occurrence)
        self.assertEqual(len(resp.data['events']), 20)
        # No two events share start == target_occ (i.e. no duplicate)
        starts = [e['start'] for e in resp.data['events']]
        self.assertNotIn(self._iso(target_occ), starts)

    def test_recurring_master_in_past_with_cancelled(self):
        """Past master + a cancelled exception → that date is skipped entirely,
        list still contains 20 events (next occurrences fill the gap)."""
        now = timezone.now().replace(microsecond=0)
        master = self._make_weekly(start=now - timedelta(days=365))

        from workspace.calendar.recurrence import next_occurrences_after
        future_occs = list(next_occurrences_after(master, after=now, limit=2))
        cancelled_occ = future_occs[0]

        Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title='Cancelled occurrence',
            start=cancelled_occ,
            end=cancelled_occ + timedelta(hours=1),
            recurrence_parent=master,
            original_start=cancelled_occ,
            is_cancelled=True,
        )

        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 20)
        # Ensure cancelled title not in list
        titles = [e['title'] for e in resp.data['events']]
        self.assertNotIn('Cancelled occurrence', titles)
        # Ensure no event is at the cancelled occ start
        starts = [e['start'] for e in resp.data['events']]
        self.assertNotIn(self._iso(cancelled_occ), starts)

    def test_recurring_respects_recurrence_end(self):
        """Weekly master, recurrence_end at week 4 → exactly 5 occurrences
        (weeks 0..4), then next_after=None."""
        now = timezone.now().replace(microsecond=0)
        self._make_weekly(
            start=now + timedelta(days=1),
            rec_end=now + timedelta(weeks=4, days=1, hours=1),
        )
        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 5)
        self.assertIsNone(resp.data['next_after'])

    def test_recurring_monthly_stride(self):
        """Monthly master → correct strides across months."""
        now = timezone.now().replace(day=1, hour=10, minute=0, second=0, microsecond=0)
        Event.objects.create(
            calendar=self.cal, owner=self.owner, title='Monthly',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
            recurrence_frequency='monthly',
            recurrence_interval=1,
        )
        resp = self._get_cursor(after=now)
        events = resp.data['events']
        self.assertEqual(len(events), 20)
        # sanity: first and last span roughly 19 months
        from dateutil.parser import parse as _p
        span_days = (_p(events[-1]['start']) - _p(events[0]['start'])).days
        self.assertGreater(span_days, 18 * 28)

    def test_recurring_interval_respected(self):
        """Bi-weekly master → 14-day stride between consecutive occurrences."""
        now = timezone.now().replace(microsecond=0)
        Event.objects.create(
            calendar=self.cal, owner=self.owner, title='BiWeekly',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
            recurrence_frequency='weekly',
            recurrence_interval=2,
        )
        resp = self._get_cursor(after=now)
        from dateutil.parser import parse as _p
        events = resp.data['events']
        diff = _p(events[1]['start']) - _p(events[0]['start'])
        self.assertEqual(diff, timedelta(days=14))

    def test_mixed_sort(self):
        """Non-recurring events and recurring occurrences merged sorted by start."""
        now = timezone.now().replace(microsecond=0)
        # Non-recurring at day 2, 4, 6
        self._make_event('N2', now + timedelta(days=2))
        self._make_event('N4', now + timedelta(days=4))
        self._make_event('N6', now + timedelta(days=6))
        # Daily recurring from day 1 → day 1, 2, 3, 4, 5, 6, ...
        Event.objects.create(
            calendar=self.cal, owner=self.owner, title='Daily',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
            recurrence_frequency='daily',
            recurrence_interval=1,
        )
        resp = self._get_cursor(after=now)
        # Compare parsed datetimes, not strings: DRF serializes UTC with a
        # "Z" suffix and `make_virtual_occurrence` uses isoformat ("+00:00").
        # Lex-sorting these strings is incorrect at the same instant.
        from dateutil.parser import parse as _p
        starts = [_p(e['start']) for e in resp.data['events']]
        self.assertEqual(starts, sorted(starts))

    def test_cancelled_at_sentinel_position_preserves_next_after(self):
        """Regression: if a cancelled exception falls at position limit+1
        (the sentinel slot), `next_after` must still be set because the
        series continues beyond. Naively over-fetching exactly `limit + 1`
        and filtering would silently drop the cursor."""
        now = timezone.now().replace(microsecond=0)
        master = self._make_weekly(start=now - timedelta(days=365))

        # Find the 21st future occurrence (position limit+1 = index 20)
        from workspace.calendar.recurrence import next_occurrences_after
        future_occs = list(next_occurrences_after(master, after=now, limit=22))
        sentinel_occ = future_occs[20]  # the 21st

        Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title='Cancelled at sentinel',
            start=sentinel_occ,
            end=sentinel_occ + timedelta(hours=1),
            recurrence_parent=master,
            original_start=sentinel_occ,
            is_cancelled=True,
        )

        resp = self._get_cursor(after=now)
        self.assertEqual(len(resp.data['events']), 20)
        # next_after MUST NOT be None — the series continues
        self.assertIsNotNone(
            resp.data['next_after'],
            msg="Cancelled exception at sentinel position silently truncated the cursor",
        )


class CursorModeFiltersTests(CursorModeMixin, APITestCase):
    """Filter params: calendar_ids, show_declined. Plus access control."""

    def test_calendar_ids_filter_non_recurring(self):
        now = timezone.now()
        cal2 = Calendar.objects.create(name='Other', owner=self.owner)
        self._make_event('InCal1', now + timedelta(days=1), calendar=self.cal)
        self._make_event('InCal2', now + timedelta(days=2), calendar=cal2)

        resp = self.client.get(self.url, {
            'after': self._iso(now),
            'limit': 20,
            'calendar_ids': str(self.cal.pk),
        })
        titles = [e['title'] for e in resp.data['events']]
        self.assertEqual(titles, ['InCal1'])

    def test_calendar_ids_filter_recurring(self):
        """Recurring master on an excluded calendar → its occurrences do not
        leak into a filtered request."""
        now = timezone.now()
        cal2 = Calendar.objects.create(name='Other', owner=self.owner)
        Event.objects.create(
            calendar=cal2, owner=self.owner, title='ExcludedWeekly',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
            recurrence_frequency='weekly',
            recurrence_interval=1,
        )

        resp = self.client.get(self.url, {
            'after': self._iso(now),
            'limit': 20,
            'calendar_ids': str(self.cal.pk),  # NOT cal2
        })
        titles = [e['title'] for e in resp.data['events']]
        self.assertNotIn('ExcludedWeekly', titles)

    def test_show_declined_false_excludes_declined(self):
        """An event where the user is a declined member is excluded by default."""
        now = timezone.now()
        other_cal = Calendar.objects.create(name='Other', owner=self.other)
        event = Event.objects.create(
            calendar=other_cal, owner=self.other, title='InvitedToThis',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        EventMember.objects.create(
            event=event, user=self.owner, status=EventMember.Status.DECLINED,
        )

        resp = self._get_cursor(after=now)
        titles = [e['title'] for e in resp.data['events']]
        self.assertNotIn('InvitedToThis', titles)

    def test_show_declined_true_includes_declined(self):
        now = timezone.now()
        other_cal = Calendar.objects.create(name='Other', owner=self.other)
        event = Event.objects.create(
            calendar=other_cal, owner=self.other, title='InvitedToThis',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        EventMember.objects.create(
            event=event, user=self.owner, status=EventMember.Status.DECLINED,
        )

        resp = self.client.get(self.url, {
            'after': self._iso(now), 'limit': 20, 'show_declined': 'true',
        })
        titles = [e['title'] for e in resp.data['events']]
        self.assertIn('InvitedToThis', titles)

    def test_show_declined_recurring(self):
        """Recurring master the user has declined → occurrences filtered out
        when show_declined=false."""
        now = timezone.now()
        other_cal = Calendar.objects.create(name='Other', owner=self.other)
        master = Event.objects.create(
            calendar=other_cal, owner=self.other, title='DeclinedWeekly',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
            recurrence_frequency='weekly',
            recurrence_interval=1,
        )
        EventMember.objects.create(
            event=master, user=self.owner, status=EventMember.Status.DECLINED,
        )

        resp = self._get_cursor(after=now)
        titles = [e['title'] for e in resp.data['events']]
        self.assertNotIn('DeclinedWeekly', titles)

    def test_access_control_other_users_events_not_visible(self):
        """Owner A requests agenda; calendar owned by user B that A has no
        membership on → none of B's events appear."""
        now = timezone.now()
        b_cal = Calendar.objects.create(name='B', owner=self.other)
        Event.objects.create(
            calendar=b_cal, owner=self.other, title='BsPrivateEvent',
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )

        resp = self._get_cursor(after=now)
        titles = [e['title'] for e in resp.data['events']]
        self.assertNotIn('BsPrivateEvent', titles)


class CursorModeStabilityTests(CursorModeMixin, APITestCase):

    def test_cursor_resumes_correctly_end_to_end(self):
        """Create 30 events, paginate through all pages → union equals full set."""
        now = timezone.now()
        for i in range(30):
            self._make_event(f'E{i:02d}', now + timedelta(hours=i + 1))

        all_titles = []
        after = self._iso(now)
        while True:
            resp = self.client.get(self.url, {'after': after, 'limit': 10})
            all_titles.extend(e['title'] for e in resp.data['events'])
            if resp.data['next_after'] is None:
                break
            after = resp.data['next_after']

        # Dedup (boundary events may appear twice; client must dedup by uuid).
        # For this test, we use title uniqueness.
        unique_titles = set(all_titles)
        self.assertEqual(len(unique_titles), 30)
        # Expected set
        expected = {f'E{i:02d}' for i in range(30)}
        self.assertEqual(unique_titles, expected)

    def test_cursor_tie_within_page(self):
        """Three events sharing the same start → all appear in the same page."""
        now = timezone.now().replace(microsecond=0)
        tied = now + timedelta(days=1)
        self._make_event('A', tied)
        self._make_event('B', tied)
        self._make_event('C', tied)

        resp = self._get_cursor(after=now)
        titles = sorted(e['title'] for e in resp.data['events'])
        self.assertEqual(titles, ['A', 'B', 'C'])

    def test_cursor_tie_at_page_boundary(self):
        """Events 20 and 21 share the same start. Page 1 returns 20 events.
        Page 2 with after=<cursor> returns the boundary event (because >=),
        so callers must dedup client-side. This test verifies the server
        does not SILENTLY DROP the tied event."""
        now = timezone.now().replace(microsecond=0)
        for i in range(19):
            self._make_event(f'E{i:02d}', now + timedelta(hours=i + 1))
        # Two events share the same start (hour 20)
        tied = now + timedelta(hours=20)
        self._make_event('E19', tied)
        self._make_event('E20', tied)

        resp1 = self._get_cursor(after=now)
        self.assertEqual(len(resp1.data['events']), 20)
        self.assertIsNotNone(resp1.data['next_after'])

        resp2 = self.client.get(self.url, {
            'after': resp1.data['next_after'], 'limit': 20,
        })
        # Page 2 must include at least one event (the boundary tied one).
        self.assertGreaterEqual(len(resp2.data['events']), 1)
        # The union of page 1 titles and page 2 titles (deduped) must contain
        # all 21 distinct titles.
        p1_titles = {e['title'] for e in resp1.data['events']}
        p2_titles = {e['title'] for e in resp2.data['events']}
        union = p1_titles | p2_titles
        self.assertEqual(len(union), 21)

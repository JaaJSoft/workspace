from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.activity import CalendarActivityProvider
from workspace.calendar.models import (
    Calendar,
    CalendarSubscription,
    Event,
    EventMember,
)

User = get_user_model()


class CalendarActivityProviderTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.bob = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

        self.ts = timezone.now()

        # Alice's public calendar (bob subscribes to it) — 2 events
        self.alice_cal = Calendar.objects.create(
            name='Alice Public', owner=self.alice,
        )
        self.alice_evt1 = Event.objects.create(
            calendar=self.alice_cal, title='Alice Event 1',
            start=self.ts, end=self.ts + timedelta(hours=1),
            owner=self.alice,
        )
        self.alice_evt2 = Event.objects.create(
            calendar=self.alice_cal, title='Alice Event 2',
            start=self.ts + timedelta(hours=2),
            end=self.ts + timedelta(hours=3),
            owner=self.alice,
        )

        # Alice's private calendar (no subscription) — 1 event
        self.alice_private_cal = Calendar.objects.create(
            name='Alice Private', owner=self.alice,
        )
        self.alice_private_evt = Event.objects.create(
            calendar=self.alice_private_cal, title='Alice Private Event',
            start=self.ts + timedelta(hours=4),
            end=self.ts + timedelta(hours=5),
            owner=self.alice,
        )

        # Bob's calendar — 1 event
        self.bob_cal = Calendar.objects.create(
            name='Bob Calendar', owner=self.bob,
        )
        self.bob_evt = Event.objects.create(
            calendar=self.bob_cal, title='Bob Event 1',
            start=self.ts, end=self.ts + timedelta(hours=1),
            owner=self.bob,
        )

        # Bob subscribes to Alice's public calendar
        CalendarSubscription.objects.create(
            user=self.bob, calendar=self.alice_cal,
        )

        self.provider = CalendarActivityProvider()

    # -- get_daily_counts ------------------------------------------------

    def test_daily_counts_own_profile(self):
        """Alice viewing her own profile sees all 3 of her events."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=None,
        )
        self.assertEqual(counts.get(today, 0), 3)

    def test_daily_counts_viewer_sees_subscribed_only(self):
        """Bob viewing Alice's profile sees only the 2 events from the subscribed calendar."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 2)

    # -- get_recent_events -----------------------------------------------

    def test_recent_events_own_profile(self):
        """Alice sees all 3 of her events."""
        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=None,
        )
        self.assertEqual(len(events), 3)

    def test_recent_events_viewer_sees_subscribed_only(self):
        """Bob sees only 2 events from Alice's subscribed calendar."""
        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 2)
        titles = {e['description'] for e in events}
        self.assertEqual(titles, {'Alice Event 1', 'Alice Event 2'})

    def test_recent_events_viewer_via_event_membership(self):
        """Bob can see a private-calendar event if he is an EventMember on it."""
        EventMember.objects.create(
            event=self.alice_private_evt, user=self.bob,
        )
        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 3)
        titles = {e['description'] for e in events}
        self.assertIn('Alice Private Event', titles)

    # -- get_stats -------------------------------------------------------

    def test_stats_own_profile(self):
        """Alice gets correct counts for her own profile."""
        stats = self.provider.get_stats(self.alice.id, viewer_id=None)
        self.assertEqual(stats['total_events'], 3)

    def test_stats_viewer_restricted(self):
        """Bob viewing Alice sees only subscribed calendar events."""
        stats = self.provider.get_stats(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(stats['total_events'], 2)

    # -- cancelled events excluded ---------------------------------------

    def test_cancelled_events_excluded(self):
        """Cancelled events are excluded from all provider methods."""
        Event.objects.create(
            calendar=self.alice_cal, title='Cancelled Event',
            start=self.ts, end=self.ts + timedelta(hours=1),
            owner=self.alice, is_cancelled=True,
        )
        today = self.ts.date()

        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=None,
        )
        self.assertEqual(counts.get(today, 0), 3)  # still 3, cancelled excluded

        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=None,
        )
        self.assertEqual(len(events), 3)

        stats = self.provider.get_stats(self.alice.id, viewer_id=None)
        self.assertEqual(stats['total_events'], 3)

"""Tests for the today's-events notification cron and its read-on-display path."""

from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.calendar.models import Event, EventMember
from workspace.calendar.tasks import notify_today_events
from workspace.notifications.models import Notification

from .test_calendar import CalendarTestMixin


class TodayEventNotificationMixin(CalendarTestMixin):
    def setUp(self):
        super().setUp()
        # The mixin's event is tomorrow; today's tests create their own.

    def tearDown(self):
        cache.clear()

    def _event_today(self, title="Standup", **kwargs):
        # Clamped inside the current local day so the fixture stays valid
        # whenever the suite runs: near midnight "now + 1h" would land on
        # tomorrow, while a fixed hour would already be over by the evening.
        # An already-started event is fine - the cron includes ongoing ones.
        end_of_today = timezone.make_aware(
            datetime.combine(timezone.localdate(), time.max),
            timezone.get_current_timezone(),
        )
        now = timezone.now()
        start = min(now + timedelta(hours=1), end_of_today - timedelta(minutes=30))
        if start + timedelta(minutes=29) <= now:
            # Final minute of the day: the clamped slot is already over, so
            # fall back to an ongoing event capped at end of day.
            start = now - timedelta(minutes=1)
        return Event.objects.create(
            calendar=self.calendar,
            title=title,
            start=start,
            end=min(start + timedelta(minutes=29), end_of_today),
            owner=self.owner,
            **kwargs,
        )

    def _unread(self, user, event=None):
        qs = Notification.objects.filter(
            recipient=user, origin="calendar", read_at__isnull=True
        )
        if event is not None:
            qs = qs.filter(event=event)
        return qs


class NotifyTodayEventsCronTests(TodayEventNotificationMixin, TestCase):
    def test_notifies_owner_of_todays_event(self):
        event = self._event_today()

        notify_today_events()

        notif = self._unread(self.owner, event).get()
        self.assertEqual(notif.title, "Standup")
        self.assertIn("Today at", notif.body)
        self.assertEqual(notif.url, f"/calendar?event={event.uuid}")
        # Informational: badge and bell, never a push.
        self.assertEqual(notif.priority, "low")

    def test_notifies_invited_member(self):
        event = self._event_today()
        EventMember.objects.create(event=event, user=self.member)

        notify_today_events()

        self.assertTrue(self._unread(self.member, event).exists())

    def test_skips_events_on_other_days(self):
        # self.event (from the mixin) starts tomorrow.
        notify_today_events()

        self.assertFalse(self._unread(self.owner).exists())

    def test_recurring_occurrence_keys_on_the_master_without_url(self):
        start = timezone.now() - timedelta(days=2)
        master = Event.objects.create(
            calendar=self.calendar,
            title="Daily sync",
            start=start,
            end=start + timedelta(hours=1),
            owner=self.owner,
            recurrence_frequency=Event.RecurrenceFrequency.DAILY,
        )

        notify_today_events()

        notif = self._unread(self.owner, master).get()
        self.assertEqual(notif.url, "")

    def test_rerun_merges_instead_of_stacking(self):
        event = self._event_today()

        notify_today_events()
        notify_today_events()

        self.assertEqual(self._unread(self.owner, event).count(), 1)

    def test_reminder_does_not_repurpose_an_existing_invitation(self):
        event = self._event_today()
        invitation = Notification.objects.create(
            recipient=self.owner,
            origin="calendar",
            icon="i",
            title="You're invited to Standup",
            event=event,
        )

        notify_today_events()

        invitation.refresh_from_db()
        self.assertEqual(invitation.title, "You're invited to Standup")
        # The reminder lands on its own row instead of merging into the
        # invitation - notify_stream only merges within a stream.
        self.assertEqual(self._unread(self.owner, event).count(), 2)


class DisplayedEventsMarkReadTests(TodayEventNotificationMixin, APITestCase):
    url = "/api/v1/events"

    def _range_params(self):
        return {
            "start": (timezone.now() - timedelta(hours=12)).isoformat(),
            "end": (timezone.now() + timedelta(hours=12)).isoformat(),
        }

    def test_range_fetch_settles_displayed_events(self):
        event = self._event_today()
        notify_today_events()
        self.assertTrue(self._unread(self.owner, event).exists())

        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._unread(self.owner, event).exists())

    def test_range_fetch_resolves_recurring_occurrences_to_the_master(self):
        start = timezone.now() - timedelta(days=2)
        master = Event.objects.create(
            calendar=self.calendar,
            title="Daily sync",
            start=start,
            end=start + timedelta(hours=1),
            owner=self.owner,
            recurrence_frequency=Event.RecurrenceFrequency.DAILY,
        )
        notify_today_events()
        self.assertTrue(self._unread(self.owner, master).exists())

        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._unread(self.owner, master).exists())

    def test_range_fetch_leaves_undisplayed_events_unread(self):
        event = self._event_today()
        notify_today_events()

        self.client.force_authenticate(self.owner)
        params = {
            "start": (timezone.now() + timedelta(days=30)).isoformat(),
            "end": (timezone.now() + timedelta(days=31)).isoformat(),
        }
        resp = self.client.get(self.url, params)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._unread(self.owner, event).exists())

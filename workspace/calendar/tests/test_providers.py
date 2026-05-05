from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Event, EventMember

from .test_calendar import CalendarTestMixin


# ---------- Pending Action Provider ----------


class CalendarPendingActionProviderTests(CalendarTestMixin, TestCase):
    """Tests for the calendar pending action provider.

    Uses a frozen 'now' at 10:00 AM to avoid flaky results near midnight.
    """

    FROZEN_NOW = timezone.make_aware(
        timezone.datetime(2026, 6, 15, 10, 0, 0),
        timezone.get_default_timezone(),
    )

    def setUp(self):
        super().setUp()
        # Move the default event to later today (14:00) so it's inside the window.
        self.event.start = self.FROZEN_NOW + timedelta(hours=4)
        self.event.end = self.FROZEN_NOW + timedelta(hours=5)
        self.event.save()

    def _counts(self, user):
        from workspace.core.module_registry import registry
        with patch('django.utils.timezone.now', return_value=self.FROZEN_NOW):
            return registry.get_pending_action_counts(user)

    def test_pending_actions_counts_todays_upcoming_events(self):
        counts = self._counts(self.owner)
        self.assertEqual(counts.get('calendar'), 1)

    def test_pending_actions_includes_events_as_member(self):
        counts = self._counts(self.member)
        self.assertEqual(counts.get('calendar'), 1)

    def test_pending_actions_excludes_past_events(self):
        self.event.start = self.FROZEN_NOW - timedelta(hours=2)
        self.event.end = self.FROZEN_NOW - timedelta(hours=1)
        self.event.save()
        counts = self._counts(self.owner)
        self.assertEqual(counts.get('calendar'), 0)

    def test_pending_actions_excludes_tomorrows_events(self):
        self.event.start = self.FROZEN_NOW + timedelta(days=1)
        self.event.end = self.FROZEN_NOW + timedelta(days=1, hours=1)
        self.event.save()
        counts = self._counts(self.owner)
        self.assertEqual(counts.get('calendar'), 0)

    def test_pending_actions_excludes_declined_invitations(self):
        em = EventMember.objects.get(event=self.event, user=self.member)
        em.status = EventMember.Status.DECLINED
        em.save()
        counts = self._counts(self.member)
        self.assertEqual(counts.get('calendar'), 0)

    def test_pending_actions_counts_multiple_todays_events(self):
        Event.objects.create(
            calendar=self.calendar,
            title='Second meeting',
            start=self.FROZEN_NOW + timedelta(hours=6),
            end=self.FROZEN_NOW + timedelta(hours=7),
            owner=self.owner,
        )
        counts = self._counts(self.owner)
        self.assertEqual(counts.get('calendar'), 2)

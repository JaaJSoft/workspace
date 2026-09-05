"""Tests for the calendar_recheck_recurrence management command."""

from datetime import UTC, datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()


class CalendarRecheckRecurrenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.cal = Calendar.objects.create(name="Test", owner=self.user)
        self.start = datetime(2026, 1, 6, 10, tzinfo=UTC)

    def _series(self, rule="RRULE:FREQ=WEEKLY;COUNT=10"):
        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Standup",
            start=self.start,
            end=self.start + timedelta(hours=1),
        )
        apply_rule(event, rule)
        event.save()
        return event

    def _desync(self, event):
        # Every write goes through apply_rule, so the only way to produce
        # drift is to bypass it - a bulk .update() writes the columns
        # directly, the way a stray raw-SQL fix or a bug elsewhere might.
        Event.objects.filter(pk=event.pk).update(
            is_recurring=False, recurrence_until=None
        )

    def test_reports_all_consistent_when_nothing_drifted(self):
        self._series()
        out = StringIO()
        call_command("calendar_recheck_recurrence", stdout=out)
        self.assertIn("All events consistent.", out.getvalue())

    def test_reports_drift_without_fixing_it(self):
        event = self._series()
        self._desync(event)

        out = StringIO()
        call_command("calendar_recheck_recurrence", stdout=out)

        self.assertIn(str(event.pk), out.getvalue())
        self.assertIn("re-run with --fix", out.getvalue())
        event.refresh_from_db()
        self.assertFalse(event.is_recurring)

    def test_fix_writes_the_recomputed_values_back(self):
        event = self._series()
        self._desync(event)

        out = StringIO()
        call_command("calendar_recheck_recurrence", "--fix", stdout=out)

        self.assertIn("Repaired 1 event(s).", out.getvalue())
        event.refresh_from_db()
        self.assertTrue(event.is_recurring)
        self.assertIsNotNone(event.recurrence_until)

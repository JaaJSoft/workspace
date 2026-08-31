from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services import event_scope
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()


class WriterInvariantTests(TestCase):
    """Every write path must leave the derived columns coherent. A writer that
    forgets fails silently in production - the event stops being expanded - so
    each path gets its own case."""

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
            end=datetime(2026, 1, 6, 11, tzinfo=UTC),
        )
        from workspace.calendar.services.recurrence_rule import apply_rule

        apply_rule(event, rule)
        event.save()
        return event

    def test_apply_rule_sets_all_three_columns(self):
        event = self._series()
        self.assertTrue(event.is_recurring)
        self.assertEqual(event.recurrence_rule, "RRULE:FREQ=WEEKLY;COUNT=10")
        self.assertIsNotNone(event.recurrence_until)

    def test_clearing_the_rule_clears_the_bound(self):
        from workspace.calendar.services.recurrence_rule import apply_rule

        event = self._series()
        apply_rule(event, "")
        self.assertFalse(event.is_recurring)
        self.assertIsNone(event.recurrence_until)

    def test_future_split_truncates_the_rule_not_just_the_bound(self):
        # The split must edit the authoritative text: leaving the rule
        # untouched and only moving the derived bound would export a series
        # to CalDAV clients that never ends.
        event = self._series()
        cut = datetime(2026, 2, 3, 10, tzinfo=UTC)
        event_scope._truncate_series(event, cut)
        event.refresh_from_db()
        self.assertIn("UNTIL=", event.recurrence_rule)
        self.assertNotIn("COUNT=", event.recurrence_rule)
        self.assertLess(event.recurrence_until, cut + (event.end - event.start))

    def test_clearing_the_rule_through_the_writer_leaves_is_recurring_false(self):
        event = self._series()
        event_scope.update_event(event, {"recurrence_rule": ""}, self.user, scope="all")
        event.refresh_from_db()
        self.assertFalse(event.is_recurring)

    def test_future_split_new_master_keeps_original_extent(self):
        # The new master continues the series past the split point; it must
        # not inherit the OLD master's post-truncation UNTIL, or the split
        # would silently cap the continuing series at the cut instead of
        # wherever the original rule actually ended.
        event = self._series(rule="RRULE:FREQ=WEEKLY;COUNT=10")
        original_start = self.start + timedelta(weeks=2)
        new_master = event_scope._update_future_occurrences(
            event, {}, self.user, original_start
        )
        self.assertIn("COUNT=10", new_master.recurrence_rule)
        self.assertNotIn("UNTIL=", new_master.recurrence_rule)


class RecurrenceEditThroughApiTests(APITestCase):
    """An edit through the REST layer must re-derive ``is_recurring`` /
    ``recurrence_until`` so the series keeps expanding correctly in the list
    endpoint - editing an unrelated field or changing the cadence must not
    leave those columns stale."""

    url = "/api/v1/events"

    def setUp(self):
        self.user = User.objects.create_user(username="carol", password="pass")
        self.cal = Calendar.objects.create(name="Test", owner=self.user)
        self.start = datetime(2026, 1, 6, 10, tzinfo=UTC)
        self.client.force_authenticate(self.user)

    def _weekly(self):
        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Standup",
            start=self.start,
            end=self.start + timedelta(hours=1),
        )
        apply_rule(event, "RRULE:FREQ=WEEKLY;INTERVAL=1")
        event.save()
        return event

    def test_editing_title_keeps_the_series_recurring(self):
        event = self._weekly()
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"scope": "all", "title": "Standup (renamed)"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertTrue(event.is_recurring)

        params = {
            "start": self.start.isoformat(),
            "end": (self.start + timedelta(weeks=4)).isoformat(),
        }
        list_resp = self.client.get(self.url, params)
        recurring = [e for e in list_resp.data if e.get("is_recurring")]
        self.assertGreaterEqual(len(recurring), 3)

    def test_changing_cadence_takes_effect_in_the_list_endpoint(self):
        event = self._weekly()
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"scope": "all", "recurrence_rule": "RRULE:FREQ=DAILY"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.recurrence_rule, "RRULE:FREQ=DAILY")

        params = {
            "start": self.start.isoformat(),
            "end": (self.start + timedelta(days=5)).isoformat(),
        }
        list_resp = self.client.get(self.url, params)
        recurring = [e for e in list_resp.data if e.get("is_recurring")]
        # Daily over 5 days must beat what the stale weekly cadence would
        # have produced (at most one occurrence in that window).
        self.assertGreater(len(recurring), 1)

    def test_complex_rule_stays_recurring(self):
        event = self._weekly()
        event_scope.update_event(
            event,
            {"recurrence_rule": "RRULE:FREQ=MONTHLY;BYDAY=2TU"},
            self.user,
            scope="all",
        )
        event.refresh_from_db()
        self.assertTrue(event.is_recurring)

from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services import event_scope

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

    def test_clearing_the_rule_on_a_backfilled_row_does_not_resurrect_is_recurring(
        self,
    ):
        # Task 2's backfill gave every legacy recurring row both a
        # recurrence_frequency and a recurrence_rule. Clearing the rule
        # through the writer must not let the transitional save() shim in
        # models.py read the stale frequency column and turn the row back
        # into a recurring series with no rule.
        event = self._series()
        event.recurrence_frequency = Event.RecurrenceFrequency.WEEKLY
        event.save(update_fields=["recurrence_frequency"])

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


class LegacyRecurrenceSyncTests(APITestCase):
    """``_sync_legacy_recurrence`` keeps recurrence_frequency/interval/end in
    step with the rule, because the query layer and the expansion engine
    still read those columns (they are not migrated until a later task).
    Blanking them on every write - the previous behaviour - made an edited
    backfilled recurring event invisible as a series."""

    url = "/api/v1/events"

    def setUp(self):
        self.user = User.objects.create_user(username="carol", password="pass")
        self.cal = Calendar.objects.create(name="Test", owner=self.user)
        self.start = datetime(2026, 1, 6, 10, tzinfo=UTC)
        self.client.force_authenticate(self.user)

    def _backfilled_weekly(self):
        # Task 2's backfill shape: both the legacy columns the readers use
        # and the rule text the writer now maintains.
        from workspace.calendar.services.recurrence_rule import apply_rule

        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Standup",
            start=self.start,
            end=self.start + timedelta(hours=1),
            recurrence_frequency="weekly",
            recurrence_interval=1,
        )
        apply_rule(event, "RRULE:FREQ=WEEKLY;INTERVAL=1")
        event.save()
        return event

    def test_editing_title_keeps_the_series_visible_to_legacy_readers(self):
        event = self._backfilled_weekly()
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"scope": "all", "title": "Standup (renamed)"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertTrue(event.is_recurring)
        self.assertEqual(event.recurrence_frequency, "weekly")

        params = {
            "start": self.start.isoformat(),
            "end": (self.start + timedelta(weeks=4)).isoformat(),
        }
        list_resp = self.client.get(self.url, params)
        recurring = [e for e in list_resp.data if e.get("is_recurring")]
        self.assertGreaterEqual(len(recurring), 3)

    def test_changing_cadence_takes_effect_in_the_list_endpoint(self):
        event = self._backfilled_weekly()
        resp = self.client.put(
            f"{self.url}/{event.uuid}",
            {"scope": "all", "recurrence_rule": "RRULE:FREQ=DAILY"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.recurrence_frequency, "daily")

        params = {
            "start": self.start.isoformat(),
            "end": (self.start + timedelta(days=5)).isoformat(),
        }
        list_resp = self.client.get(self.url, params)
        recurring = [e for e in list_resp.data if e.get("is_recurring")]
        # Daily over 5 days must beat what the stale weekly cadence would
        # have produced (at most one occurrence in that window).
        self.assertGreater(len(recurring), 1)

    def test_complex_rule_stays_recurring_while_legacy_columns_go_none(self):
        event = self._backfilled_weekly()
        event_scope.update_event(
            event,
            {"recurrence_rule": "RRULE:FREQ=MONTHLY;BYDAY=2TU"},
            self.user,
            scope="all",
        )
        event.refresh_from_db()
        self.assertTrue(event.is_recurring)
        self.assertIsNone(event.recurrence_frequency)

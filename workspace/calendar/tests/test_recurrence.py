from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.recurrence import (
    expand_recurring_events,
    make_virtual_occurrence,
    next_occurrences_after,
    occurrences_in_range,
)
from workspace.calendar.services.recurrence_rule import apply_rule

from .test_calendar import CalendarTestMixin

User = get_user_model()


def _until(dt):
    """RFC 5545 UNTIL token for an aware datetime, in UTC."""
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class OccurrencesInRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.cal = Calendar.objects.create(name="Test", owner=self.user)

    def _make_master(self, rule, start, end=None):
        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Recurring",
            start=start,
            end=end,
        )
        apply_rule(event, rule)
        event.save()
        return event

    def test_daily_recurrence(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=DAILY", now)
        range_start = now
        range_end = now + timedelta(days=3)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertEqual(len(occs), 3)

    def test_weekly_recurrence(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=WEEKLY", now)
        range_start = now
        range_end = now + timedelta(weeks=3)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertEqual(len(occs), 3)

    def test_monthly_recurrence(self):
        now = timezone.now().replace(day=1, hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=MONTHLY", now)
        range_start = now
        range_end = now + timedelta(days=90)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertGreaterEqual(len(occs), 3)

    def test_interval_respected(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=DAILY;INTERVAL=2", now)
        range_start = now
        range_end = now + timedelta(days=6)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertEqual(len(occs), 3)  # day 0, 2, 4

    def test_recurrence_end_respected(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        rec_end = now + timedelta(days=2)
        master = self._make_master(f"RRULE:FREQ=DAILY;UNTIL={_until(rec_end)}", now)
        range_start = now
        range_end = now + timedelta(days=10)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertLessEqual(len(occs), 3)

    def test_unparseable_rule_yields_nothing(self):
        now = timezone.now()
        master = self._make_master("RRULE:FREQ=DAILY", now)
        master.recurrence_rule = "bogus"
        occs = list(occurrences_in_range(master, now, now + timedelta(days=5)))
        self.assertEqual(occs, [])

    def test_event_with_duration_overlaps(self):
        """Events with end time: yield if occurrence end > range_start."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=1)
        master = self._make_master("RRULE:FREQ=DAILY", start, end)
        range_start = now
        range_end = now + timedelta(days=1)
        occs = list(occurrences_in_range(master, range_start, range_end))
        # The first occurrence starts 2h before range_start but ends 1h after
        self.assertGreaterEqual(len(occs), 1)

    def test_past_occurrences_excluded(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=10)
        master = self._make_master("RRULE:FREQ=DAILY", start)
        range_start = now
        range_end = now + timedelta(days=2)
        occs = list(occurrences_in_range(master, range_start, range_end))
        for occ in occs:
            self.assertGreaterEqual(occ, range_start)

    def test_old_master_narrow_window_exact_occurrences(self):
        """A years-old daily master expanded over a narrow window yields
        exactly the in-window occurrences."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=730)
        master = self._make_master("RRULE:FREQ=DAILY", start)
        range_start = now
        range_end = now + timedelta(days=3)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertEqual(occs, [now, now + timedelta(days=1), now + timedelta(days=2)])

    def test_old_master_with_interval_keeps_series_phase(self):
        """The window jump must not re-anchor the series: occurrences of an
        every-3-days master stay aligned to the master's start."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        # 100 days ago; 100 % 3 == 1, so the next in-phase occurrence
        # after `now` is now + 2 days.
        start = now - timedelta(days=100)
        master = self._make_master("RRULE:FREQ=DAILY;INTERVAL=3", start)
        range_start = now
        range_end = now + timedelta(days=7)
        occs = list(occurrences_in_range(master, range_start, range_end))
        self.assertEqual(occs, [now + timedelta(days=2), now + timedelta(days=5)])

    def test_occurrence_ending_exactly_at_range_start_excluded(self):
        """Overlap is strict: an occurrence ending exactly at range_start
        does not overlap the window."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=2)
        master = self._make_master("RRULE:FREQ=DAILY", start, now)
        range_start = now
        range_end = now + timedelta(days=1)
        occs = list(occurrences_in_range(master, range_start, range_end))
        # Day 0 ends exactly at range_start (excluded); day 1 overlaps.
        self.assertEqual(occs, [start + timedelta(days=1)])

    def test_old_master_with_duration_overlap_at_window_edge(self):
        """An old master whose in-progress occurrence straddles range_start
        is still yielded after the window jump."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=365, hours=2)
        end = start + timedelta(hours=3)  # daily 3h event, straddles `now`
        master = self._make_master("RRULE:FREQ=DAILY", start, end)
        range_start = now
        range_end = now + timedelta(days=1)
        occs = list(occurrences_in_range(master, range_start, range_end))
        # The occurrence that started 2h ago (ends in 1h) plus tomorrow's
        # occurrence starting at now+22h, which is inside the window.
        self.assertEqual(
            occs,
            [now - timedelta(hours=2), now + timedelta(hours=22)],
        )

    def test_occurrence_starting_at_range_start_included(self):
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=5)
        master = self._make_master("RRULE:FREQ=DAILY", start)
        occs = list(occurrences_in_range(master, now, now + timedelta(days=1)))
        self.assertEqual(occs, [now])


class MakeVirtualOccurrenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.cal = Calendar.objects.create(name="Test", owner=self.user)

    def test_returns_dict_with_expected_keys(self):
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="Meeting",
            start=now,
            end=now + timedelta(hours=1),
            recurrence_frequency="daily",
        )
        occ = make_virtual_occurrence(master, now)
        self.assertEqual(occ["title"], "Meeting")
        self.assertTrue(occ["is_recurring"])
        self.assertFalse(occ["is_exception"])
        self.assertEqual(occ["master_event_id"], str(master.uuid))

    def test_computes_end_from_duration(self):
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="Meeting",
            start=now,
            end=now + timedelta(hours=2),
            recurrence_frequency="daily",
        )
        occ_start = now + timedelta(days=1)
        occ = make_virtual_occurrence(master, occ_start)
        expected_end = (occ_start + timedelta(hours=2)).isoformat()
        self.assertEqual(occ["end"], expected_end)

    def test_end_is_none_when_no_master_end(self):
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="All day",
            start=now,
            end=None,
            recurrence_frequency="daily",
        )
        occ = make_virtual_occurrence(master, now)
        self.assertIsNone(occ["end"])


class NextOccurrencesAfterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="pass")
        self.cal = Calendar.objects.create(name="Test", owner=self.user)

    def _make_master(self, rule, start, end=None):
        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Recurring",
            start=start,
            end=end,
        )
        apply_rule(event, rule)
        event.save()
        return event

    def test_future_master_yields_limit_occurrences(self):
        """Weekly master starting tomorrow → first 20 occurrences."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=WEEKLY", now + timedelta(days=1))
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(len(occs), 20)
        # First is tomorrow, then weekly
        self.assertEqual(occs[0], now + timedelta(days=1))
        self.assertEqual(occs[1], now + timedelta(days=8))

    def test_past_master_skips_past_occurrences(self):
        """Master starting 1 year ago → occurrences are all >= after."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=WEEKLY", now - timedelta(days=365))
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(len(occs), 20)
        for occ in occs:
            self.assertGreaterEqual(occ, now)

    def test_past_master_keeps_series_phase(self):
        """Skipping past occurrences must not re-anchor the series."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        # 100 days ago, every 3 days; 100 % 3 == 1, so the next in-phase
        # occurrence at or after `now` is now + 2 days.
        master = self._make_master(
            "RRULE:FREQ=DAILY;INTERVAL=3", now - timedelta(days=100)
        )
        occs = list(next_occurrences_after(master, after=now, limit=3))
        self.assertEqual(
            occs,
            [
                now + timedelta(days=2),
                now + timedelta(days=5),
                now + timedelta(days=8),
            ],
        )

    def test_past_master_occurrence_exactly_at_after_included(self):
        """`after` is inclusive: an in-phase occurrence at `after` is yielded."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=DAILY", now - timedelta(days=30))
        occs = list(next_occurrences_after(master, after=now, limit=2))
        self.assertEqual(occs, [now, now + timedelta(days=1)])

    def test_limit_respected(self):
        """limit=5 → exactly 5 occurrences."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=DAILY", now)
        occs = list(next_occurrences_after(master, after=now, limit=5))
        self.assertEqual(len(occs), 5)

    def test_recurrence_end_stops_iteration(self):
        """Weekly master with recurrence_end exactly at week 3 → 4 occurrences
        (weeks 0, 1, 2, 3) because dateutil.rrule treats `until` as inclusive."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master(
            f"RRULE:FREQ=WEEKLY;UNTIL={_until(now + timedelta(weeks=3))}", now
        )
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(len(occs), 4)  # weeks 0, 1, 2, 3

    def test_interval_respected(self):
        """Bi-weekly master → 14-day stride."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        master = self._make_master("RRULE:FREQ=WEEKLY;INTERVAL=2", now)
        occs = list(next_occurrences_after(master, after=now, limit=3))
        self.assertEqual(len(occs), 3)
        self.assertEqual(occs[1] - occs[0], timedelta(days=14))
        self.assertEqual(occs[2] - occs[1], timedelta(days=14))

    def test_non_recurring_master_yields_nothing(self):
        """Non-recurring Event passed in → generator yields nothing."""
        now = timezone.now()
        master = Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="One-off",
            start=now,
            end=now + timedelta(hours=1),
        )
        occs = list(next_occurrences_after(master, after=now, limit=20))
        self.assertEqual(occs, [])


# ---------- Recurrence ----------


class RecurrenceCreateTests(CalendarTestMixin, APITestCase):
    """Tests for creating recurring events."""

    url = "/api/v1/events"

    def _event_data(self, **overrides):
        data = {
            "calendar_id": str(self.calendar.uuid),
            "title": "Weekly Standup",
            "start": (timezone.now() + timedelta(days=1)).isoformat(),
            "end": (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
            "recurrence_rule": "RRULE:FREQ=WEEKLY",
        }
        data.update(overrides)
        return data

    def test_create_recurring_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, self._event_data(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["is_recurring"])
        event = Event.objects.get(title="Weekly Standup")
        self.assertEqual(event.recurrence_rule, "RRULE:FREQ=WEEKLY")

    def test_created_recurring_event_expands_to_a_later_occurrence(self):
        # Asserting on is_recurring alone doesn't exercise the legacy-gated
        # readers (views/events.py's list endpoint filters on
        # recurrence_frequency, not is_recurring) - only a window landing on
        # a LATER occurrence, not the row's own start, proves the event was
        # actually filed into the recurring bucket and expanded from there.
        self.client.force_authenticate(self.owner)
        start = timezone.now() + timedelta(days=1)
        resp = self.client.post(
            self.url,
            self._event_data(
                start=start.isoformat(),
                end=(start + timedelta(hours=1)).isoformat(),
            ),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        params = {
            "start": (start + timedelta(weeks=2)).isoformat(),
            "end": (start + timedelta(weeks=3)).isoformat(),
        }
        list_resp = self.client.get(self.url, params)
        titles = [e["title"] for e in list_resp.data if e.get("is_recurring")]
        self.assertIn("Weekly Standup", titles)

    def test_create_non_recurring_unchanged(self):
        self.client.force_authenticate(self.owner)
        data = {
            "calendar_id": str(self.calendar.uuid),
            "title": "One-off Event",
            "start": (timezone.now() + timedelta(days=2)).isoformat(),
            "end": (timezone.now() + timedelta(days=2, hours=1)).isoformat(),
        }
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.data["recurrence_frequency"])
        self.assertFalse(resp.data["is_recurring"])


class RecurrenceExpansionTests(CalendarTestMixin, APITestCase):
    """Tests for recurring event expansion in GET list."""

    url = "/api/v1/events"

    def _create_recurring(
        self,
        freq="weekly",
        interval=1,
        start_offset=0,
        end_offset=None,
        recurrence_end=None,
        now=None,
    ):
        base = now if now is not None else timezone.now()
        start = base + timedelta(days=start_offset)
        event = Event(
            calendar=self.calendar,
            title="Recurring",
            start=start,
            end=start + timedelta(hours=1),
            owner=self.owner,
        )
        rule = f"RRULE:FREQ={freq.upper()}"
        if interval != 1:
            rule += f";INTERVAL={interval}"
        if recurrence_end:
            rule += f";UNTIL={_until(recurrence_end)}"
        apply_rule(event, rule)
        event.save()
        return event

    def test_weekly_event_expands(self):
        now = timezone.now()
        self._create_recurring(freq="weekly", start_offset=0, now=now)
        self.client.force_authenticate(self.owner)
        params = {
            "start": now.isoformat(),
            "end": (now + timedelta(days=28)).isoformat(),
        }
        resp = self.client.get(self.url, params)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        recurring = [e for e in resp.data if e.get("is_recurring")]
        self.assertGreaterEqual(len(recurring), 4)

    def test_recurring_with_end_date(self):
        now = timezone.now()
        recurrence_end = now + timedelta(days=14)
        self._create_recurring(
            freq="weekly", start_offset=0, recurrence_end=recurrence_end, now=now
        )
        self.client.force_authenticate(self.owner)
        params = {
            "start": now.isoformat(),
            "end": (now + timedelta(days=60)).isoformat(),
        }
        resp = self.client.get(self.url, params)
        recurring = [e for e in resp.data if e.get("is_recurring")]
        self.assertLessEqual(len(recurring), 3)

    def test_daily_with_interval(self):
        now = timezone.now()
        self._create_recurring(freq="daily", interval=2, start_offset=0, now=now)
        self.client.force_authenticate(self.owner)
        params = {
            "start": now.isoformat(),
            "end": (now + timedelta(days=10)).isoformat(),
        }
        resp = self.client.get(self.url, params)
        recurring = [e for e in resp.data if e.get("is_recurring")]
        # Every 2 days over 10 days = 5 or 6 occurrences
        self.assertGreaterEqual(len(recurring), 5)
        self.assertLessEqual(len(recurring), 6)

    def test_virtual_occurrence_has_is_recurring(self):
        self._create_recurring(freq="weekly", start_offset=0)
        self.client.force_authenticate(self.owner)
        params = {
            "start": timezone.now().isoformat(),
            "end": (timezone.now() + timedelta(days=14)).isoformat(),
        }
        resp = self.client.get(self.url, params)
        recurring = [e for e in resp.data if e.get("is_recurring")]
        self.assertGreater(len(recurring), 0)
        for occ in recurring:
            self.assertTrue(occ["is_recurring"])
            self.assertIn("master_event_id", occ)
            self.assertIn("original_start", occ)


class RecurrenceExceptionTests(CalendarTestMixin, APITestCase):
    """Tests for scope-aware edit and delete of recurring events."""

    def _create_weekly(self):
        # Backfilled shape (Task 2): both the legacy columns the query layer
        # still reads and the rule text the writer now maintains.
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        event = Event(
            calendar=self.calendar,
            title="Weekly",
            start=start,
            end=start + timedelta(hours=1),
            owner=self.owner,
            recurrence_frequency="weekly",
            recurrence_interval=1,
        )
        apply_rule(event, "RRULE:FREQ=WEEKLY;INTERVAL=1")
        event.save()
        return event

    def url(self, event_id):
        return f"/api/v1/events/{event_id}"

    def test_delete_this_creates_cancelled_exception(self):
        master = self._create_weekly()
        occ_start = (master.start + timedelta(weeks=1)).isoformat()
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(
            f"{self.url(master.uuid)}?scope=this&original_start={occ_start}",
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        exc = Event.objects.filter(recurrence_parent=master, is_cancelled=True)
        self.assertEqual(exc.count(), 1)

    def test_delete_future_truncates_master(self):
        master = self._create_weekly()
        occ_start = (master.start + timedelta(weeks=2)).isoformat()
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(
            f"{self.url(master.uuid)}?scope=future&original_start={occ_start}",
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        master.refresh_from_db()
        self.assertIn("UNTIL=", master.recurrence_rule)

    def test_delete_all_deletes_everything(self):
        master = self._create_weekly()
        master_id = master.uuid
        # Create an exception
        Event.objects.create(
            calendar=self.calendar,
            title="Exception",
            start=master.start + timedelta(weeks=1),
            owner=self.owner,
            recurrence_parent=master,
            original_start=master.start + timedelta(weeks=1),
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(f"{self.url(master_id)}?scope=all")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Event.objects.filter(uuid=master_id).exists())
        self.assertFalse(Event.objects.filter(recurrence_parent_id=master_id).exists())

    def test_edit_this_creates_exception(self):
        master = self._create_weekly()
        occ_start = (master.start + timedelta(weeks=1)).isoformat()
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(master.uuid),
            {"scope": "this", "original_start": occ_start, "title": "Modified"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        exc = Event.objects.filter(recurrence_parent=master)
        self.assertEqual(exc.count(), 1)
        self.assertEqual(exc.first().title, "Modified")

    def test_edit_future_creates_new_master(self):
        master = self._create_weekly()
        occ_start = (master.start + timedelta(weeks=2)).isoformat()
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(master.uuid),
            {"scope": "future", "original_start": occ_start, "title": "Future Series"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        master.refresh_from_db()
        self.assertIn("UNTIL=", master.recurrence_rule)
        self.assertEqual(resp.data["title"], "Future Series")
        self.assertTrue(resp.data["is_recurring"])

    def test_edit_all_updates_master(self):
        master = self._create_weekly()
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(master.uuid),
            {"scope": "all", "title": "Updated Series"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        master.refresh_from_db()
        self.assertEqual(master.title, "Updated Series")

    def test_cancelled_occurrence_not_in_expansion(self):
        master = self._create_weekly()
        occ_start = master.start + timedelta(weeks=1)
        Event.objects.create(
            calendar=self.calendar,
            title="Cancelled",
            start=occ_start,
            owner=self.owner,
            recurrence_parent=master,
            original_start=occ_start,
            is_cancelled=True,
        )
        self.client.force_authenticate(self.owner)
        params = {
            "start": timezone.now().isoformat(),
            "end": (timezone.now() + timedelta(days=21)).isoformat(),
        }
        resp = self.client.get("/api/v1/events", params)
        recurring = [e for e in resp.data if e.get("is_recurring")]
        starts = [e["original_start"] for e in recurring if e.get("original_start")]
        self.assertNotIn(occ_start.isoformat(), starts)

    def test_modified_occurrence_in_expansion(self):
        master = self._create_weekly()
        occ_start = master.start + timedelta(weeks=1)
        Event.objects.create(
            calendar=self.calendar,
            title="Special Meeting",
            start=occ_start,
            end=occ_start + timedelta(hours=2),
            owner=self.owner,
            recurrence_parent=master,
            original_start=occ_start,
        )
        self.client.force_authenticate(self.owner)
        params = {
            "start": timezone.now().isoformat(),
            "end": (timezone.now() + timedelta(days=21)).isoformat(),
        }
        resp = self.client.get("/api/v1/events", params)
        titles = [e["title"] for e in resp.data if e.get("is_recurring")]
        self.assertIn("Special Meeting", titles)


class RecurrenceServiceTests(CalendarTestMixin, TestCase):
    """Unit tests for the recurrence expansion service."""

    def _create_weekly(self, **kwargs):
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        defaults = dict(
            calendar=self.calendar,
            title="Weekly",
            start=start,
            end=start + timedelta(hours=1),
            owner=self.owner,
        )
        defaults.update(kwargs)
        event = Event(**defaults)
        apply_rule(event, "RRULE:FREQ=WEEKLY")
        event.save()
        return event

    def test_occurrences_in_range_weekly(self):
        master = self._create_weekly()
        range_start = master.start
        range_end = master.start + timedelta(days=28)
        dates = list(occurrences_in_range(master, range_start, range_end))
        self.assertEqual(len(dates), 4)

    def test_expand_with_cancelled_exception(self):
        master = self._create_weekly()
        occ_start = master.start + timedelta(weeks=1)
        Event.objects.create(
            calendar=self.calendar,
            title="Cancelled",
            start=occ_start,
            owner=self.owner,
            recurrence_parent=master,
            original_start=occ_start,
            is_cancelled=True,
        )
        range_start = master.start
        range_end = master.start + timedelta(days=21)
        from django.db.models import Prefetch

        masters = (
            Event.objects.filter(pk=master.pk)
            .prefetch_related(
                Prefetch("members", queryset=EventMember.objects.select_related("user"))
            )
            .select_related("owner", "calendar")
        )
        result = expand_recurring_events(masters, range_start, range_end)
        starts = [r["original_start"] for r in result]
        self.assertNotIn(occ_start.isoformat(), starts)

    def test_expand_with_modified_exception(self):
        master = self._create_weekly()
        occ_start = master.start + timedelta(weeks=1)
        Event.objects.create(
            calendar=self.calendar,
            title="Modified Meeting",
            start=occ_start,
            end=occ_start + timedelta(hours=2),
            owner=self.owner,
            recurrence_parent=master,
            original_start=occ_start,
        )
        range_start = master.start
        range_end = master.start + timedelta(days=21)
        from django.db.models import Prefetch

        masters = (
            Event.objects.filter(pk=master.pk)
            .prefetch_related(
                Prefetch("members", queryset=EventMember.objects.select_related("user"))
            )
            .select_related("owner", "calendar")
        )
        result = expand_recurring_events(masters, range_start, range_end)
        titles = [r["title"] for r in result]
        self.assertIn("Modified Meeting", titles)


class WallClockExpansionTests(TestCase):
    """Series with Event.timezone keep their local wall clock across DST.

    Europe/Paris switches to summer time on Sunday 2026-03-29 02:00:
    09:00 local is 08:00Z before the change and 07:00Z after it.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="wc", password="p")
        self.cal = Calendar.objects.create(name="W", owner=self.user)

    def _master(self, tz="Europe/Paris", freq="daily", start=None, **kwargs):
        dtstart = start or datetime(2026, 3, 27, 8, 0, tzinfo=UTC)  # 09:00 Paris
        event = Event(
            calendar=self.cal,
            owner=self.user,
            title="Standup",
            start=dtstart,
            end=dtstart + timedelta(hours=1),
            timezone=tz,
            **kwargs,
        )
        apply_rule(event, f"RRULE:FREQ={freq.upper()}")
        event.save()
        return event

    def _expand(self, master, start, end):
        return list(occurrences_in_range(master, start, end))

    def test_daily_series_keeps_wall_clock_across_dst(self):
        master = self._master()
        occs = self._expand(
            master,
            datetime(2026, 3, 27, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 31, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            [o.astimezone(UTC).isoformat() for o in occs],
            [
                "2026-03-27T08:00:00+00:00",
                "2026-03-28T08:00:00+00:00",
                "2026-03-29T07:00:00+00:00",
                "2026-03-30T07:00:00+00:00",
            ],
        )

    def test_occurrences_are_utc_instants(self):
        master = self._master()
        occs = self._expand(
            master,
            datetime(2026, 3, 29, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 30, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0].utcoffset(), timedelta(0))

    def test_empty_timezone_keeps_legacy_fixed_step(self):
        master = self._master(tz="")
        occs = self._expand(
            master,
            datetime(2026, 3, 27, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 31, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            [o.isoformat() for o in occs],
            [
                "2026-03-27T08:00:00+00:00",
                "2026-03-28T08:00:00+00:00",
                "2026-03-29T08:00:00+00:00",
                "2026-03-30T08:00:00+00:00",
            ],
        )

    def test_old_master_anchor_keeps_wall_clock(self):
        # Master a year before the window: exercises the fast-forward anchor.
        master = self._master(start=datetime(2025, 3, 27, 8, 0, tzinfo=UTC))
        occs = self._expand(
            master,
            datetime(2026, 3, 29, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 31, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            [o.astimezone(UTC).isoformat() for o in occs],
            ["2026-03-29T07:00:00+00:00", "2026-03-30T07:00:00+00:00"],
        )

    def test_weekly_series_keeps_wall_clock_across_dst(self):
        master = self._master(freq="weekly")
        occs = self._expand(
            master,
            datetime(2026, 3, 27, 0, 0, tzinfo=UTC),
            datetime(2026, 4, 11, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            [o.astimezone(UTC).isoformat() for o in occs],
            [
                "2026-03-27T08:00:00+00:00",
                "2026-04-03T07:00:00+00:00",
                "2026-04-10T07:00:00+00:00",
            ],
        )

    def test_next_occurrences_after_crosses_dst(self):
        master = self._master()
        occs = list(
            next_occurrences_after(master, datetime(2026, 3, 28, 12, 0, tzinfo=UTC), 2)
        )
        self.assertEqual(
            [o.astimezone(UTC).isoformat() for o in occs],
            ["2026-03-29T07:00:00+00:00", "2026-03-30T07:00:00+00:00"],
        )

    def test_exception_matches_post_dst_occurrence(self):
        master = self._master()
        Event.objects.create(
            calendar=self.cal,
            owner=self.user,
            title="Standup (moved)",
            start=datetime(2026, 3, 29, 9, 0, tzinfo=UTC),
            recurrence_parent=master,
            original_start=datetime(2026, 3, 29, 7, 0, tzinfo=UTC),
        )
        occs = expand_recurring_events(
            Event.objects.filter(pk=master.pk).select_related("owner"),
            datetime(2026, 3, 29, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 30, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(len(occs), 1)
        self.assertTrue(occs[0]["is_exception"])
        self.assertEqual(occs[0]["title"], "Standup (moved)")

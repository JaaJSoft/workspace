"""The agenda: GET /api/v1/events with a `start` and no `end`.

The list of what starts from an instant on has no end while a series
repeats, so it is read a page at a time: `limit` and `offset` over a bare
array, the `X-Has-More` header saying whether a row follows. Every page of
one reading names the same `start`.
"""

from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.services.event_listing import AGENDA_DEPTH
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()


def _until(dt):
    """RFC 5545 UNTIL token for an aware datetime, in UTC."""
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class AgendaMixin:
    """Common setup for the agenda tests."""

    url = "/api/v1/events"

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@test.com",
            password="pass123",
        )
        self.other = User.objects.create_user(
            username="other",
            email="other@test.com",
            password="pass123",
        )
        self.cal = Calendar.objects.create(name="Work", owner=self.owner)
        self.client.force_authenticate(self.owner)

    def _iso(self, dt):
        return dt.isoformat()

    def _get_agenda(self, start, **extra):
        params = {"start": self._iso(start), "limit": 20, **extra}
        return self.client.get(self.url, params)

    def _starts(self, resp):
        return [datetime.fromisoformat(e["start"]) for e in resp.data]

    def _make_event(self, title, start, end=None, calendar=None):
        return Event.objects.create(
            calendar=calendar or self.cal,
            owner=self.owner,
            title=title,
            start=start,
            end=end or start + timedelta(hours=1),
        )


class AgendaEmptyTests(AgendaMixin, APITestCase):
    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(user=None)  # clear auth
        now = timezone.now()
        resp = self._get_agenda(start=now)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_database_returns_empty_page(self):
        now = timezone.now()
        resp = self._get_agenda(start=now)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])
        self.assertEqual(resp["X-Has-More"], "false")


class AgendaNonRecurringTests(AgendaMixin, APITestCase):
    def test_fewer_than_limit_returns_all(self):
        now = timezone.now()
        for i in range(5):
            self._make_event(f"E{i}", now + timedelta(days=i + 1))
        resp = self._get_agenda(start=now)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 5)
        self.assertEqual(resp["X-Has-More"], "false")

    def test_exact_limit_has_no_next_page(self):
        now = timezone.now()
        for i in range(20):
            self._make_event(f"E{i}", now + timedelta(days=i + 1))
        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 20)
        self.assertEqual(resp["X-Has-More"], "false")

    def test_pagination(self):
        now = timezone.now()
        for i in range(30):
            self._make_event(f"E{i:02d}", now + timedelta(days=i + 1))

        resp1 = self._get_agenda(start=now)
        self.assertEqual(
            [e["title"] for e in resp1.data], [f"E{i:02d}" for i in range(20)]
        )
        self.assertEqual(resp1["X-Has-More"], "true")

        resp2 = self._get_agenda(start=now, offset=20)
        self.assertEqual(
            [e["title"] for e in resp2.data], [f"E{i:02d}" for i in range(20, 30)]
        )
        self.assertEqual(resp2["X-Has-More"], "false")

    def test_past_events_excluded(self):
        now = timezone.now()
        for i in range(10):
            self._make_event(f"Past{i}", now - timedelta(days=i + 1))
        for i in range(5):
            self._make_event(f"Future{i}", now + timedelta(days=i + 1))
        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 5)
        for e in resp.data:
            self.assertTrue(e["title"].startswith("Future"))

    def test_limit_param_respected(self):
        now = timezone.now()
        for i in range(10):
            self._make_event(f"E{i}", now + timedelta(days=i + 1))
        resp = self._get_agenda(start=now, limit=3)
        self.assertEqual(len(resp.data), 3)
        self.assertEqual(resp["X-Has-More"], "true")

    def test_a_limit_past_the_maximum_is_refused(self):
        resp = self._get_agenda(start=timezone.now(), limit=1001)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["limit"])

    def test_the_agenda_is_read_to_a_fixed_depth_from_one_start(self):
        """The work of a page grows with its offset: past the depth, the
        reader moves `start` forward instead."""
        now = timezone.now()
        deepest = self._get_agenda(start=now, limit=20, offset=AGENDA_DEPTH - 20)
        self.assertEqual(deepest.status_code, status.HTTP_200_OK)
        past = self._get_agenda(start=now, limit=20, offset=AGENDA_DEPTH - 19)
        self.assertEqual(past.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(past.data), ["offset"])
        # A window has an end: it is read whole whatever the offset.
        window = self._get_agenda(
            start=now, end=self._iso(now + timedelta(days=1)), offset=10**9
        )
        self.assertEqual(window.status_code, status.HTTP_200_OK)

    def test_events_sorted_by_start_ascending(self):
        now = timezone.now()
        # Create in non-sorted order
        self._make_event("C", now + timedelta(days=3))
        self._make_event("A", now + timedelta(days=1))
        self._make_event("B", now + timedelta(days=2))
        resp = self._get_agenda(start=now)
        titles = [e["title"] for e in resp.data]
        self.assertEqual(titles, ["A", "B", "C"])


class AgendaRecurringTests(AgendaMixin, APITestCase):
    """Edge cases for recurring event expansion in the agenda."""

    def _make_weekly(self, start, rec_end=None, title="Weekly"):
        event = Event(
            calendar=self.cal,
            owner=self.owner,
            title=title,
            start=start,
            end=start + timedelta(hours=1),
        )
        rule = "RRULE:FREQ=WEEKLY"
        if rec_end:
            rule += f";UNTIL={_until(rec_end)}"
        apply_rule(event, rule)
        event.save()
        return event

    def test_recurring_weekly_future_master(self):
        """Weekly master starting tomorrow → 20 occurrences, and a 21st to
        come. Each names its series and its slot."""
        now = timezone.now().replace(microsecond=0)
        master = self._make_weekly(start=now + timedelta(days=1))
        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 20)
        self.assertEqual(resp["X-Has-More"], "true")
        self.assertEqual({e["uuid"] for e in resp.data}, {str(master.uuid)})
        self.assertEqual(
            [datetime.fromisoformat(e["original_start"]) for e in resp.data],
            [master.start + timedelta(weeks=week) for week in range(20)],
        )

    def test_a_series_is_paged_through_like_any_list(self):
        now = timezone.now().replace(microsecond=0)
        master = self._make_weekly(start=now + timedelta(days=1))
        second = self._get_agenda(start=now, limit=5, offset=5)
        self.assertEqual(
            self._starts(second),
            [master.start + timedelta(weeks=week) for week in range(5, 10)],
        )
        self.assertEqual(second["X-Has-More"], "true")

    def test_recurring_master_in_past(self):
        """Master with start 1 year ago, no recurrence_end → 20 FUTURE
        occurrences returned. Master itself not in list; no occ has start < now."""
        now = timezone.now().replace(microsecond=0)
        self._make_weekly(start=now - timedelta(days=365))
        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 20)
        for start in self._starts(resp):
            self.assertGreaterEqual(start, now)

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

        Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title="Moved meeting",
            start=target_occ + timedelta(hours=2),  # moved 2 hours later
            end=target_occ + timedelta(hours=3),
            recurrence_parent=master,
            original_start=target_occ,
        )

        resp = self._get_agenda(start=now)
        titles = [e["title"] for e in resp.data]
        self.assertIn("Moved meeting", titles)
        # The count is still 20 (exception replaced the virtual occurrence)
        self.assertEqual(len(resp.data), 20)
        # Nothing is drawn at the slot it was moved from: no duplicate.
        self.assertNotIn(target_occ, self._starts(resp))
        moved = next(e for e in resp.data if e["title"] == "Moved meeting")
        self.assertEqual(moved["uuid"], str(master.uuid))
        self.assertEqual(datetime.fromisoformat(moved["original_start"]), target_occ)
        self.assertTrue(moved["is_exception"])

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
            title="Cancelled occurrence",
            start=cancelled_occ,
            end=cancelled_occ + timedelta(hours=1),
            recurrence_parent=master,
            original_start=cancelled_occ,
            is_cancelled=True,
        )

        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 20)
        # Ensure cancelled title not in list
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("Cancelled occurrence", titles)
        # Ensure no event is at the cancelled occ start
        self.assertNotIn(cancelled_occ, self._starts(resp))

    def test_recurring_respects_recurrence_end(self):
        """Weekly master, recurrence_end at week 4 → exactly 5 occurrences
        (weeks 0..4), and nothing after them."""
        now = timezone.now().replace(microsecond=0)
        self._make_weekly(
            start=now + timedelta(days=1),
            rec_end=now + timedelta(weeks=4, days=1, hours=1),
        )
        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 5)
        self.assertEqual(resp["X-Has-More"], "false")

    def test_recurring_monthly_stride(self):
        """Monthly master → correct strides across months."""
        now = timezone.now().replace(day=1, hour=10, minute=0, second=0, microsecond=0)
        event = Event(
            calendar=self.cal,
            owner=self.owner,
            title="Monthly",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        apply_rule(event, "RRULE:FREQ=MONTHLY")
        event.save()
        resp = self._get_agenda(start=now)
        events = resp.data
        self.assertEqual(len(events), 20)
        # sanity: first and last span roughly 19 months
        from dateutil.parser import parse as _p

        span_days = (_p(events[-1]["start"]) - _p(events[0]["start"])).days
        self.assertGreater(span_days, 18 * 28)

    def test_recurring_interval_respected(self):
        """Bi-weekly master → 14-day stride between consecutive occurrences."""
        now = timezone.now().replace(microsecond=0)
        event = Event(
            calendar=self.cal,
            owner=self.owner,
            title="BiWeekly",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        apply_rule(event, "RRULE:FREQ=WEEKLY;INTERVAL=2")
        event.save()
        resp = self._get_agenda(start=now)
        from dateutil.parser import parse as _p

        events = resp.data
        diff = _p(events[1]["start"]) - _p(events[0]["start"])
        self.assertEqual(diff, timedelta(days=14))

    def test_mixed_sort(self):
        """Non-recurring events and recurring occurrences merged sorted by start."""
        now = timezone.now().replace(microsecond=0)
        # Non-recurring at day 2, 4, 6
        self._make_event("N2", now + timedelta(days=2))
        self._make_event("N4", now + timedelta(days=4))
        self._make_event("N6", now + timedelta(days=6))
        # Daily recurring from day 1 → day 1, 2, 3, 4, 5, 6, ...
        event = Event(
            calendar=self.cal,
            owner=self.owner,
            title="Daily",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        apply_rule(event, "RRULE:FREQ=DAILY")
        event.save()
        resp = self._get_agenda(start=now)
        starts = self._starts(resp)
        self.assertEqual(starts, sorted(starts))
        # Two rows at one instant keep the order of their creation.
        self.assertEqual(
            [e["title"] for e in resp.data][:6],
            ["Daily", "N2", "Daily", "Daily", "N4", "Daily"],
        )

    def test_all_day_event_sorts_with_timed_events(self):
        """An all-day event serializes its start as a bare date, which parses
        naive; merging it with a timed (aware) event must not blow up."""
        now = timezone.now().replace(microsecond=0)
        self._make_event("Timed", now + timedelta(days=1))
        day = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0)
        Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title="All day",
            start=day,
            end=day + timedelta(days=1),
            all_day=True,
        )
        resp = self._get_agenda(start=now)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([e["title"] for e in resp.data], ["Timed", "All day"])

    def test_cancelled_at_sentinel_position_still_announces_a_next_page(self):
        """Regression: if a cancelled occurrence falls at position limit+1
        (the row read to know whether a page follows), `X-Has-More` must
        still be true because the series continues beyond. Naively walking
        exactly `limit + 1` slots and filtering would end the list there."""
        now = timezone.now().replace(microsecond=0)
        master = self._make_weekly(start=now - timedelta(days=365))

        # Find the 21st future occurrence (position limit+1 = index 20)
        from workspace.calendar.recurrence import next_occurrences_after

        future_occs = list(next_occurrences_after(master, after=now, limit=22))
        sentinel_occ = future_occs[20]  # the 21st

        Event.objects.create(
            calendar=self.cal,
            owner=self.owner,
            title="Cancelled at sentinel",
            start=sentinel_occ,
            end=sentinel_occ + timedelta(hours=1),
            recurrence_parent=master,
            original_start=sentinel_occ,
            is_cancelled=True,
        )

        resp = self._get_agenda(start=now)
        self.assertEqual(len(resp.data), 20)
        self.assertEqual(
            resp["X-Has-More"],
            "true",
            msg="A cancelled occurrence at the sentinel position ended the list",
        )


class AgendaFiltersTests(AgendaMixin, APITestCase):
    """Filter params: calendar, include_declined. Plus access control."""

    def test_calendar_filter_non_recurring(self):
        now = timezone.now()
        cal2 = Calendar.objects.create(name="Other", owner=self.owner)
        self._make_event("InCal1", now + timedelta(days=1), calendar=self.cal)
        self._make_event("InCal2", now + timedelta(days=2), calendar=cal2)

        resp = self._get_agenda(start=now, calendar=str(self.cal.pk))
        titles = [e["title"] for e in resp.data]
        self.assertEqual(titles, ["InCal1"])

    def test_calendar_filter_recurring(self):
        """Recurring master on an excluded calendar → its occurrences do not
        leak into a filtered request."""
        now = timezone.now()
        cal2 = Calendar.objects.create(name="Other", owner=self.owner)
        event = Event(
            calendar=cal2,
            owner=self.owner,
            title="ExcludedWeekly",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        apply_rule(event, "RRULE:FREQ=WEEKLY")
        event.save()

        resp = self._get_agenda(start=now, calendar=str(self.cal.pk))
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("ExcludedWeekly", titles)

    def test_an_unseen_calendar_lists_nothing(self):
        now = timezone.now()
        foreign = Calendar.objects.create(name="Foreign", owner=self.other)
        Event.objects.create(
            calendar=foreign,
            owner=self.other,
            title="NotYours",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )

        resp = self._get_agenda(start=now, calendar=str(foreign.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_malformed_calendar_is_rejected(self):
        resp = self._get_agenda(start=timezone.now(), calendar="not-a-uuid")
        self.assertEqual(resp.status_code, 400)

    def test_invitations_are_listed_with_the_calendars_under_none(self):
        """What the page asks for: the calendars ticked, and `none` for the
        events one is only invited to, which sit in no calendar of theirs."""
        now = timezone.now()
        self._make_event("Mine", now + timedelta(days=1))
        other_cal = Calendar.objects.create(name="Other", owner=self.other)
        invited = Event.objects.create(
            calendar=other_cal,
            owner=self.other,
            title="InvitedToThis",
            start=now + timedelta(days=2),
        )
        EventMember.objects.create(event=invited, user=self.owner)

        ticked = self._get_agenda(start=now, calendar=f"{self.cal.pk},none")
        self.assertEqual([e["title"] for e in ticked.data], ["Mine", "InvitedToThis"])
        alone = self._get_agenda(start=now, calendar=str(self.cal.pk))
        self.assertEqual([e["title"] for e in alone.data], ["Mine"])

    def test_declined_events_are_left_out_by_default(self):
        """An event where the user is a declined member is excluded by default."""
        now = timezone.now()
        other_cal = Calendar.objects.create(name="Other", owner=self.other)
        event = Event.objects.create(
            calendar=other_cal,
            owner=self.other,
            title="InvitedToThis",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        EventMember.objects.create(
            event=event,
            user=self.owner,
            status=EventMember.Status.DECLINED,
        )

        resp = self._get_agenda(start=now)
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("InvitedToThis", titles)

    def test_include_declined_lists_them(self):
        now = timezone.now()
        other_cal = Calendar.objects.create(name="Other", owner=self.other)
        event = Event.objects.create(
            calendar=other_cal,
            owner=self.other,
            title="InvitedToThis",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        EventMember.objects.create(
            event=event,
            user=self.owner,
            status=EventMember.Status.DECLINED,
        )

        resp = self._get_agenda(start=now, include_declined="true")
        titles = [e["title"] for e in resp.data]
        self.assertIn("InvitedToThis", titles)

    def test_declined_recurring(self):
        """Recurring master the user has declined → occurrences left out
        unless include_declined asks for them."""
        now = timezone.now()
        other_cal = Calendar.objects.create(name="Other", owner=self.other)
        master = Event(
            calendar=other_cal,
            owner=self.other,
            title="DeclinedWeekly",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )
        apply_rule(master, "RRULE:FREQ=WEEKLY")
        master.save()
        EventMember.objects.create(
            event=master,
            user=self.owner,
            status=EventMember.Status.DECLINED,
        )

        resp = self._get_agenda(start=now)
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("DeclinedWeekly", titles)

    def test_access_control_other_users_events_not_visible(self):
        """Owner A requests agenda; calendar owned by user B that A has no
        membership on → none of B's events appear."""
        now = timezone.now()
        b_cal = Calendar.objects.create(name="B", owner=self.other)
        Event.objects.create(
            calendar=b_cal,
            owner=self.other,
            title="BsPrivateEvent",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, hours=1),
        )

        resp = self._get_agenda(start=now)
        titles = [e["title"] for e in resp.data]
        self.assertNotIn("BsPrivateEvent", titles)


class AgendaPagingStabilityTests(AgendaMixin, APITestCase):
    def _walk(self, start, limit):
        """Every title the agenda serves, page after page, as served."""
        titles = []
        for number in range(40):
            resp = self._get_agenda(start=start, limit=limit, offset=number * limit)
            titles += [e["title"] for e in resp.data]
            if resp["X-Has-More"] == "false":
                return titles
        self.fail(f"the pages never reached the end: {titles}")

    def test_pages_cover_every_event_once_in_order(self):
        now = timezone.now()
        for i in range(30):
            self._make_event(f"E{i:02d}", now + timedelta(hours=i + 1))

        self.assertEqual(self._walk(now, 10), [f"E{i:02d}" for i in range(30)])

    def test_tied_events_beyond_a_page_are_each_served_once(self):
        """More events share one start than a page holds: the order needs a
        tie-break that holds from one page to the next, or one of them would
        be served twice and another never."""
        now = timezone.now().replace(microsecond=0)
        tied = now + timedelta(days=1)
        for title in ("A", "B", "C"):
            self._make_event(title, tied)
        self._make_event("D", tied + timedelta(hours=1))

        titles = self._walk(now, 1)

        self.assertEqual(sorted(titles[:3]), ["A", "B", "C"])
        self.assertEqual(titles[3:], ["D"])

    def test_tie_within_page(self):
        """Three events sharing the same start → all appear in the same page."""
        now = timezone.now().replace(microsecond=0)
        tied = now + timedelta(days=1)
        self._make_event("A", tied)
        self._make_event("B", tied)
        self._make_event("C", tied)

        resp = self._get_agenda(start=now)
        titles = sorted(e["title"] for e in resp.data)
        self.assertEqual(titles, ["A", "B", "C"])

    def test_tie_at_page_boundary(self):
        """Events 20 and 21 share the same start: one closes the first page,
        the other opens the second."""
        now = timezone.now().replace(microsecond=0)
        for i in range(19):
            self._make_event(f"E{i:02d}", now + timedelta(hours=i + 1))
        tied = now + timedelta(hours=20)
        self._make_event("E19", tied)
        self._make_event("E20", tied)

        resp1 = self._get_agenda(start=now)
        self.assertEqual(len(resp1.data), 20)
        self.assertEqual(resp1["X-Has-More"], "true")

        resp2 = self._get_agenda(start=now, offset=20)
        self.assertEqual(len(resp2.data), 1)
        titles = [e["title"] for e in [*resp1.data, *resp2.data]]
        self.assertEqual(sorted(titles), [f"E{i:02d}" for i in range(21)])

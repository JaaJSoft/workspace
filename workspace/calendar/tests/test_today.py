from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.services.recurrence_rule import apply_rule
from workspace.calendar.today import (
    INVITATIONS_LIMIT,
    pending_invitations,
    todays_events,
)

User = get_user_model()

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


class TodaysEventsTests(TestCase):
    def test_today_is_the_users_local_day(self):
        user = User.objects.create_user(username="eventuser", password="pw")
        calendar = Calendar.objects.create(name="Work", owner=user)
        for label in (
            datetime(2026, 1, 31, tzinfo=UTC),
            datetime(2026, 2, 1, tzinfo=UTC),
        ):
            Event.objects.create(
                calendar=calendar,
                owner=user,
                title=f"all-day {label.day}",
                start=label,
                all_day=True,
            )
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        now = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)

        titles = [event.title for event in todays_events(user, now, PARIS).items]

        self.assertEqual(titles, ["all-day 1"])


class PendingInvitationsTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.guest = User.objects.create_user(username="guest", password="pw")
        self.calendar = Calendar.objects.create(name="Team", owner=self.organizer)

    def _invite(self, title, start, status=EventMember.Status.PENDING, **fields):
        event = Event.objects.create(
            calendar=self.calendar,
            owner=self.organizer,
            title=title,
            start=start,
            end=fields.pop("end", start + timedelta(hours=1)),
            **fields,
        )
        EventMember.objects.create(event=event, user=self.guest, status=status)
        return event

    def _titles(self):
        return [e.title for e in pending_invitations(self.guest, NOW, PARIS).items]

    def test_unanswered_invitations_ahead_are_listed_soonest_first(self):
        self._invite("Later", NOW + timedelta(days=3))
        self._invite("Sooner", NOW + timedelta(days=1))

        self.assertEqual(self._titles(), ["Sooner", "Later"])

    def test_answered_invitations_are_not(self):
        self._invite("Accepted", NOW + timedelta(days=1), EventMember.Status.ACCEPTED)
        self._invite("Declined", NOW + timedelta(days=1), EventMember.Status.DECLINED)

        self.assertEqual(self._titles(), [])

    def test_an_event_that_is_over_or_cancelled_is_not(self):
        self._invite("Yesterday", NOW - timedelta(days=1))
        self._invite("Cancelled", NOW + timedelta(days=1), is_cancelled=True)

        self.assertEqual(self._titles(), [])

    def test_an_event_under_way_is_still_listed(self):
        self._invite("Workshop", NOW - timedelta(hours=1), end=NOW + timedelta(hours=1))

        self.assertEqual(self._titles(), ["Workshop"])

    def test_todays_all_day_event_is_listed_all_day(self):
        # The day label of Nov 21: midnight UTC, hours before NOW.
        self._invite(
            "Offsite",
            datetime(2026, 11, 21, tzinfo=UTC),
            end=datetime(2026, 11, 22, tzinfo=UTC),
            all_day=True,
        )

        self.assertEqual(self._titles(), ["Offsite"])

    def test_someone_elses_invitation_is_not_listed(self):
        other = User.objects.create_user(username="other", password="pw")
        event = self._invite("Mine", NOW + timedelta(days=1))
        EventMember.objects.create(event=event, user=other)

        titles = [e.title for e in pending_invitations(other, NOW, PARIS).items]

        self.assertEqual(titles, ["Mine"])
        EventMember.objects.filter(user=self.guest).update(
            status=EventMember.Status.ACCEPTED
        )
        self.assertEqual(self._titles(), [])

    def test_a_series_is_dated_by_its_next_occurrence(self):
        start = NOW.replace(hour=8) - timedelta(days=30)
        series = Event(
            calendar=self.calendar,
            owner=self.organizer,
            title="Weekly sync",
            start=start,
            end=start + timedelta(hours=1),
        )
        apply_rule(series, "RRULE:FREQ=WEEKLY")
        series.save()
        EventMember.objects.create(event=series, user=self.guest)

        (row,) = pending_invitations(self.guest, NOW, PARIS).items

        self.assertEqual(row.title, "Weekly sync")
        self.assertGreaterEqual(row.next_start, NOW)
        self.assertLess(row.next_start, NOW + timedelta(days=7))

    def test_a_series_that_ended_is_not_listed(self):
        start = NOW - timedelta(days=30)
        series = Event(
            calendar=self.calendar,
            owner=self.organizer,
            title="Old sync",
            start=start,
            end=start + timedelta(hours=1),
        )
        apply_rule(series, "RRULE:FREQ=DAILY;COUNT=3")
        series.save()
        EventMember.objects.create(event=series, user=self.guest)

        self.assertEqual(self._titles(), [])

    def _series(self, title, start, rule, end=None, all_day=False):
        series = Event(
            calendar=self.calendar,
            owner=self.organizer,
            title=title,
            start=start,
            end=end,
            all_day=all_day,
        )
        apply_rule(series, rule)
        series.save()
        EventMember.objects.create(event=series, user=self.guest)
        return series

    def _exception(self, series, original_start, **fields):
        return Event.objects.create(
            calendar=self.calendar,
            owner=self.organizer,
            title=series.title,
            start=fields.pop("start", original_start),
            end=fields.pop("end", original_start + timedelta(hours=1)),
            recurrence_parent=series,
            original_start=original_start,
            **fields,
        )

    def _next_starts(self):
        return {
            e.title: e.next_start
            for e in pending_invitations(self.guest, NOW, PARIS).items
        }

    def test_an_all_day_series_ending_today_is_listed_all_day(self):
        # No end: the series' bound is the start of its last day, hours before NOW.
        self._series(
            "Retreat",
            datetime(2026, 11, 18, tzinfo=UTC),
            "RRULE:FREQ=DAILY;COUNT=4",
            all_day=True,
        )

        self.assertEqual(
            self._next_starts(), {"Retreat": datetime(2026, 11, 21, tzinfo=UTC)}
        )

    def test_a_series_occurrence_under_way_is_listed(self):
        # The last occurrence runs 08:00-10:00 UTC; NOW is 09:00.
        start = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
        self._series(
            "Daily sync",
            start,
            "RRULE:FREQ=DAILY;COUNT=3",
            end=start + timedelta(hours=2),
        )

        self.assertEqual(
            self._next_starts(),
            {"Daily sync": datetime(2026, 11, 21, 8, 0, tzinfo=UTC)},
        )

    def test_a_cancelled_occurrence_is_skipped(self):
        start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
        series = self._series(
            "Lunch", start, "RRULE:FREQ=DAILY", end=start + timedelta(hours=1)
        )
        self._exception(series, start + timedelta(days=1), is_cancelled=True)

        self.assertEqual(self._next_starts(), {"Lunch": start + timedelta(days=2)})

    def test_a_moved_occurrence_answers_with_its_new_start(self):
        start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
        series = self._series(
            "Lunch", start, "RRULE:FREQ=DAILY", end=start + timedelta(hours=1)
        )
        today = start + timedelta(days=1)
        self._exception(series, today, start=today + timedelta(hours=3))

        self.assertEqual(self._next_starts(), {"Lunch": today + timedelta(hours=3)})

    def test_a_series_whose_remaining_occurrences_are_cancelled_is_left_out(self):
        start = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
        series = self._series(
            "Training",
            start,
            "RRULE:FREQ=DAILY;COUNT=3",
            end=start + timedelta(hours=1),
        )
        self._exception(series, start + timedelta(days=2), is_cancelled=True)

        self.assertEqual(self._next_starts(), {})

    def test_the_list_is_capped(self):
        for day in range(INVITATIONS_LIMIT + 2):
            self._invite(f"Event {day}", NOW + timedelta(days=day + 1))

        rows = pending_invitations(self.guest, NOW, PARIS)

        self.assertEqual(len(rows.items), INVITATIONS_LIMIT)
        self.assertEqual(rows.total, INVITATIONS_LIMIT + 2)

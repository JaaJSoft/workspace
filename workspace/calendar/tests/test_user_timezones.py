"""Calendar reads and writes for users in different timezones.

All-day events are stored at UTC midnight of their day label (see
``services/timezones.py``). Every path below either compares those labels
against someone's local day or turns a typed date into one, and each test
pins a zone on the side of UTC where the naive instant comparison breaks.
"""

import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from workspace.calendar.activity import CalendarActivityProvider
from workspace.calendar.ai_tools import (
    CalendarToolProvider,
    CancelEventParams,
    CheckAvailabilityParams,
    CreateEventParams,
    ListUpcomingEventsParams,
    SearchEventsParams,
    UpdateEventParams,
)
from workspace.calendar.models import Calendar, Event
from workspace.calendar.services import recurrence_rule as rr
from workspace.calendar.services.recurrence_rule import apply_rule
from workspace.calendar.tasks import notify_today_events
from workspace.calendar.upcoming import get_upcoming_for_user, get_upcoming_page
from workspace.mail.serializers import MailExtractionSerializer
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()

PARIS = ZoneInfo("Europe/Paris")
NEW_YORK = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")


def label(year, month, day):
    return datetime(year, month, day, tzinfo=UTC)


class _CalendarUser(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tz-user", password="pw")
        self.calendar = Calendar.objects.create(name="Work", owner=self.user)

    def tearDown(self):
        cache.clear()
        timezone.deactivate()

    def _all_day(self, title, day, days=1, **kwargs):
        return Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title=title,
            start=day,
            end=day + timedelta(days=days),
            all_day=True,
            **kwargs,
        )

    def _timed(self, title, start, hours=1):
        return Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title=title,
            start=start,
            end=start + timedelta(hours=hours),
        )


class UpcomingTodayAllDayTests(_CalendarUser):
    """The dashboard widget and the morning cron read today's events."""

    def _titles(self, now, tz):
        return [e.title for e in get_upcoming_for_user(self.user, now, tz)]

    def test_yesterdays_all_day_event_is_over_after_local_midnight_east_of_utc(self):
        # 00:30 on Feb 1 in Paris is still Jan 31 in UTC. The Jan 31 all-day
        # event ends at Feb 1 00:00 UTC, after "now", yet its day is over.
        self._all_day("Jan 31", label(2026, 1, 31))
        self._all_day("Feb 1", label(2026, 2, 1))
        now = datetime(2026, 2, 1, 0, 30, tzinfo=PARIS)

        self.assertEqual(self._titles(now, PARIS), ["Feb 1"])

    def test_todays_all_day_event_lasts_the_whole_local_evening_west_of_utc(self):
        # 21:00 on Feb 1 in New York is already Feb 2 in UTC.
        self._all_day("Feb 1", label(2026, 2, 1))
        self._all_day("Feb 2", label(2026, 2, 2))
        now = datetime(2026, 2, 1, 21, 0, tzinfo=NEW_YORK)

        self.assertEqual(self._titles(now, NEW_YORK), ["Feb 1"])

    def test_multi_day_all_day_event_covers_each_of_its_days(self):
        self._all_day("Trip", label(2026, 2, 1), days=3)
        now = datetime(2026, 2, 3, 22, 0, tzinfo=NEW_YORK)

        self.assertEqual(self._titles(now, NEW_YORK), ["Trip"])

    def test_daily_all_day_series_shows_todays_occurrence(self):
        master = Event(
            calendar=self.calendar,
            owner=self.user,
            title="Daily focus",
            start=label(2026, 1, 1),
            end=label(2026, 1, 2),
            all_day=True,
        )
        apply_rule(master, "RRULE:FREQ=DAILY")
        master.save()
        now = datetime(2026, 2, 1, 15, 0, tzinfo=PARIS)

        events = get_upcoming_for_user(self.user, now, PARIS)

        self.assertEqual([e.start for e in events], [label(2026, 2, 1)])

    def test_all_day_events_come_first(self):
        self._timed("Breakfast", datetime(2026, 2, 1, 8, 0, tzinfo=TOKYO))
        self._all_day("Holiday", label(2026, 2, 1))
        now = datetime(2026, 2, 1, 7, 0, tzinfo=TOKYO)

        self.assertEqual(self._titles(now, TOKYO), ["Holiday", "Breakfast"])


class AgendaCursorAllDayTests(_CalendarUser):
    """``GET /api/v1/events?after=now`` - the agenda view."""

    def _page(self, after, tz, limit=20, after_id=None):
        return get_upcoming_page(
            self.user, after=after, limit=limit, tz=tz, after_id=after_id
        )

    def _walk(self, after, tz, limit):
        """Every title the agenda serves, page after page, as served."""
        seen, after_id = [], None
        for _ in range(20):
            page, cursor = self._page(after, tz, limit=limit, after_id=after_id)
            seen += [e["title"] for e in page]
            if cursor is None:
                break
            after, after_id = cursor
        else:
            self.fail(f"the cursor never reached the end: {seen}")
        return seen

    def test_todays_all_day_event_stays_listed_after_utc_midnight(self):
        self._all_day("Holiday", label(2026, 2, 1))
        page, _ = self._page(datetime(2026, 2, 1, 10, 0, tzinfo=PARIS), PARIS)

        self.assertEqual([e["title"] for e in page], ["Holiday"])

    def test_tomorrows_all_day_event_sorts_after_tonights_events(self):
        # Feb 2 00:00 UTC is Feb 1 19:00 in New York: sorted by storage
        # instant, Feb 2's all-day event would sit among Feb 1's evening.
        self._all_day("Feb 2 holiday", label(2026, 2, 2))
        self._timed("Feb 1 dinner", datetime(2026, 2, 1, 20, 0, tzinfo=NEW_YORK))
        page, _ = self._page(datetime(2026, 2, 1, 18, 0, tzinfo=NEW_YORK), NEW_YORK)

        self.assertEqual([e["title"] for e in page], ["Feb 1 dinner", "Feb 2 holiday"])

    def test_cursor_walks_every_event_once_in_order(self):
        tz = NEW_YORK
        for day in range(2, 6):
            self._all_day(f"all-day {day}", label(2026, 2, day))
            self._timed(f"evening {day}", datetime(2026, 2, day, 21, 0, tzinfo=tz))
        after = datetime(2026, 2, 1, 12, 0, tzinfo=tz)

        self.assertEqual(
            self._walk(after, tz, limit=3),
            [f"{kind} {day}" for day in range(2, 6) for kind in ("all-day", "evening")],
        )

    def test_cursor_gets_past_a_day_with_more_all_day_events_than_a_page(self):
        # Every all-day event of a day shares one position in the agenda,
        # so only the event id can tell the cursor which of them it served.
        tz = PARIS
        for name in ("A", "B", "C"):
            self._all_day(f"all-day {name}", label(2026, 2, 2))
        self._timed("dinner", datetime(2026, 2, 2, 20, 0, tzinfo=tz))

        walked = self._walk(datetime(2026, 2, 1, 12, 0, tzinfo=tz), tz, limit=1)

        self.assertEqual(sorted(walked[:3]), ["all-day A", "all-day B", "all-day C"])
        self.assertEqual(walked[3:], ["dinner"])


@patch("django.utils.timezone.now")
class MorningCronUserTimezoneTests(_CalendarUser):
    def _bodies(self):
        return list(
            Notification.objects.filter(
                recipient=self.user, origin="calendar"
            ).values_list("body", flat=True)
        )

    def test_runs_in_each_users_own_morning(self, now):
        now.return_value = datetime(2026, 3, 10, 7, 15, tzinfo=UTC)  # 16:15 Tokyo
        set_setting(self.user, "core", "timezone", "Asia/Tokyo")
        self._timed("Lunch", datetime(2026, 3, 10, 12, 30, tzinfo=TOKYO))

        notify_today_events()
        self.assertEqual(self._bodies(), [])

        now.return_value = datetime(2026, 3, 9, 22, 15, tzinfo=UTC)  # 07:15 Tokyo
        notify_today_events()
        self.assertEqual(self._bodies(), ["Today at 12:30"])

    def test_all_day_event_is_judged_on_the_users_local_day(self, now):
        # 07:15 in Tokyo is still Mar 9 in UTC.
        now.return_value = datetime(2026, 3, 10, 7, 15, tzinfo=TOKYO)
        set_setting(self.user, "core", "timezone", "Asia/Tokyo")
        self._all_day("Mar 9", label(2026, 3, 9))
        self._all_day("Mar 10", label(2026, 3, 10))

        notify_today_events()

        titles = Notification.objects.filter(recipient=self.user).values_list(
            "title", flat=True
        )
        self.assertEqual(list(titles), ["Mar 10"])


class ActivityAllDayDateTests(_CalendarUser):
    def test_all_day_event_shows_its_own_day_west_of_utc(self):
        self._all_day("Holiday", label(2026, 8, 5))
        timezone.activate(NEW_YORK)

        events = CalendarActivityProvider().get_recent_events(user_id=self.user.pk)

        self.assertEqual(events[0]["meta_text"], "Wed 5 Aug")


class RecurrenceUntilTimezoneTests(SimpleTestCase):
    def tearDown(self):
        timezone.deactivate()

    def test_date_until_reaches_the_picker_as_a_bare_day(self):
        # As midnight UTC, the picker would show 28 Feb west of UTC and
        # write it back a day earlier on every save.
        simple = rr.to_simple_json("RRULE:FREQ=DAILY;UNTIL=20260301")

        self.assertEqual(simple["until"], "2026-03-01")

    def test_date_until_summary_is_the_same_day_everywhere(self):
        timezone.activate(NEW_YORK)

        self.assertEqual(
            rr.describe("RRULE:FREQ=DAILY;UNTIL=20260301"),
            "Every day, until 1 March 2026",
        )

    def test_datetime_until_summary_is_read_on_the_readers_day(self):
        # What the picker writes for "until 1 March" from New York.
        timezone.activate(NEW_YORK)

        self.assertEqual(
            rr.describe("RRULE:FREQ=WEEKLY;UNTIL=20260302T045959Z"),
            "Every week, until 1 March 2026",
        )


class FloatingUntilTests(SimpleTestCase):
    """A floating UNTIL (no zone) is the series' own wall clock: a day."""

    def tearDown(self):
        timezone.deactivate()

    def test_summary_keeps_its_day_for_every_reader(self):
        timezone.activate(NEW_YORK)

        self.assertEqual(
            rr.describe("RRULE:FREQ=DAILY;UNTIL=20260301T000000"),
            "Every day, until 1 March 2026",
        )

    def test_picker_receives_its_day(self):
        simple = rr.to_simple_json("RRULE:FREQ=DAILY;UNTIL=20260301T000000")

        self.assertEqual(simple["until"], "2026-03-01")


class MailExtractionAllDayTests(_CalendarUser):
    def test_all_day_target_is_sent_as_its_day_label(self):
        from workspace.mail.models import MailExtraction

        event = self._all_day("Conference", label(2026, 8, 5))
        extraction = MailExtraction(kind=MailExtraction.Kind.EVENT)
        extraction.target = event
        timezone.activate(NEW_YORK)

        target = MailExtractionSerializer(extraction).data["target"]

        self.assertEqual((target["start"], target["end"]), ("2026-08-05", "2026-08-06"))


class CalendarAiToolTimezoneTests(_CalendarUser):
    def setUp(self):
        super().setUp()
        self.provider = CalendarToolProvider()

    def _call(self, name, params):
        return getattr(self.provider, name)(
            params, user=self.user, bot=None, conversation_id=None, context={}
        )

    def _zone(self, name):
        set_setting(self.user, "core", "timezone", name)

    def test_create_all_day_event_lands_on_the_typed_day_east_of_utc(self):
        self._zone("Europe/Paris")

        result = self._call(
            "create_event",
            CreateEventParams(title="Holiday", start="2030-07-05", all_day=True),
        )

        event = Event.objects.get(title="Holiday")
        self.assertEqual(event.start, label(2030, 7, 5))
        self.assertIn("on 2030-07-05 ", result)

    def test_move_all_day_event_keeps_the_typed_day_east_of_utc(self):
        self._zone("Europe/Paris")
        event = self._all_day("Holiday", label(2030, 7, 5))

        result = self._call(
            "update_event",
            UpdateEventParams(
                event_id=event.uuid, scope="all", start="2030-07-10", end="2030-07-11"
            ),
        )

        event.refresh_from_db()
        self.assertEqual(
            (event.start, event.end), (label(2030, 7, 10), label(2030, 7, 11))
        )
        self.assertIn("now on 2030-07-10 ", result)

    def test_create_all_day_event_with_two_times_on_one_day_lasts_that_day(self):
        self._zone("Europe/Paris")

        self._call(
            "create_event",
            CreateEventParams(
                title="Offsite",
                start="2030-07-05T09:00",
                end="2030-07-05T17:00",
                all_day=True,
            ),
        )

        event = Event.objects.get(title="Offsite")
        self.assertEqual(
            (event.start, event.end), (label(2030, 7, 5), label(2030, 7, 6))
        )

    def test_availability_reports_a_moved_occurrence_only_at_its_new_time(self):
        self._zone("Europe/Paris")
        start = datetime(2030, 7, 1, 9, 0, tzinfo=PARIS)
        master = Event(
            calendar=self.calendar,
            owner=self.user,
            title="Weekly sync",
            start=start,
            end=start + timedelta(hours=1),
            timezone="Europe/Paris",
        )
        apply_rule(master, "RRULE:FREQ=WEEKLY")
        master.save()
        moved_from = start + timedelta(weeks=1)
        Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Weekly sync",
            start=moved_from + timedelta(hours=3),
            end=moved_from + timedelta(hours=4),
            recurrence_parent=master,
            original_start=moved_from,
        )

        result = json.loads(
            self._call(
                "check_availability",
                CheckAvailabilityParams(
                    start="2030-07-08T09:00", end="2030-07-08T10:00"
                ),
            )
        )

        self.assertTrue(result["available"])

    def test_cancel_occurrence_reply_names_the_local_day(self):
        self._zone("Europe/Paris")
        start = datetime(2030, 7, 2, 0, 30, tzinfo=PARIS)  # Monday 22:30 UTC
        master = Event(
            calendar=self.calendar,
            owner=self.user,
            title="Night shift",
            start=start,
            end=start + timedelta(hours=1),
            timezone="Europe/Paris",
        )
        apply_rule(master, "RRULE:FREQ=WEEKLY")
        master.save()

        result = self._call(
            "cancel_event",
            CancelEventParams(
                event_id=master.uuid,
                scope="this",
                # The form list_upcoming_events hands the model.
                original_start=start.astimezone(UTC).isoformat(),
                confirm=True,
            ),
        )

        self.assertIn("on 2030-07-02.", result)

    def test_search_reports_local_wall_clock_times(self):
        self._zone("Europe/Paris")
        self._timed("Dentist", datetime(2030, 7, 5, 9, 0, tzinfo=PARIS))

        result = json.loads(
            self._call("search_events", SearchEventsParams(query="Dentist"))
        )

        self.assertEqual(result[0]["start"], "2030-07-05 09:00")

    def test_availability_ignores_yesterdays_all_day_event_east_of_utc(self):
        # 08:00 on Mar 22 in Tokyo is 23:00 UTC on Mar 21, inside the UTC
        # span of Mar 21's all-day event.
        self._zone("Asia/Tokyo")
        self._all_day("Mar 21", label(2030, 3, 21))

        result = json.loads(
            self._call(
                "check_availability",
                CheckAvailabilityParams(
                    start="2030-03-22T08:00", end="2030-03-22T09:00"
                ),
            )
        )

        self.assertTrue(result["available"])

    def test_availability_sees_todays_all_day_event_late_west_of_utc(self):
        # 20:00 on Mar 21 in New York is already Mar 22 in UTC.
        self._zone("America/New_York")
        self._all_day("Mar 21", label(2030, 3, 21))

        result = json.loads(
            self._call(
                "check_availability",
                CheckAvailabilityParams(
                    start="2030-03-21T20:00", end="2030-03-21T21:00"
                ),
            )
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["events"][0]["start"], "2030-03-21")

    @patch("django.utils.timezone.now")
    def test_upcoming_lists_todays_all_day_event_from_a_worker(self, now):
        # Tools run in Celery, where the active zone is UTC.
        now.return_value = datetime(2030, 7, 5, 10, 0, tzinfo=PARIS)
        self._zone("Europe/Paris")
        self._all_day("Holiday", label(2030, 7, 5))

        result = self._call(
            "list_upcoming_events", ListUpcomingEventsParams(days_ahead=1)
        )

        self.assertIn("Holiday", result)


class AllDayLabelTests(SimpleTestCase):
    def test_label_is_utc_midnight_of_the_day(self):
        from workspace.calendar.services.timezones import all_day_label

        self.assertEqual(all_day_label(date(2026, 2, 1)), label(2026, 2, 1))

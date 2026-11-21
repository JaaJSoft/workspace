"""A subscribed feed is read object by object: a series keeps its exceptions."""

from datetime import UTC, datetime
from unittest import expectedFailure
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.services.ics_sync import sync_external_calendar
from workspace.users.services.settings import set_setting

from .ics_exports import (
    MOVED_FROM,
    MOVED_TO,
    OCCURRENCES,
    PRODUCERS,
    SERIES_UID,
    export,
    occurrence_starts,
)

User = get_user_model()


def _feed(*vevents, header=()):
    body = "".join(
        "BEGIN:VEVENT\r\n" + "".join(f"{line}\r\n" for line in lines) + "END:VEVENT\r\n"
        for lines in vevents
    )
    head = "".join(f"{line}\r\n" for line in header)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{head}{body}END:VCALENDAR\r\n"


SERIES = [
    "UID:series@example.com",
    "DTSTART;TZID=Europe/Paris:20260302T090000",
    "DTEND;TZID=Europe/Paris:20260302T100000",
    "RRULE:FREQ=WEEKLY;COUNT=8",
    "EXDATE;TZID=Europe/Paris:20260323T090000",
    "SUMMARY:Weekly sync",
]
MOVED = [
    "UID:series@example.com",
    "RECURRENCE-ID;TZID=Europe/Paris:20260316T090000",
    "DTSTART;TZID=Europe/Paris:20260317T140000",
    "DTEND;TZID=Europe/Paris:20260317T150000",
    "SUMMARY:Weekly sync (moved)",
]


class FeedTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="feeds", password="pw")
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        self.calendar = Calendar.objects.create(name="Feed", owner=self.user)
        self.external = ExternalCalendar.objects.create(
            calendar=self.calendar, url="https://feeds.test/team.ics"
        )

    def tearDown(self):
        cache.clear()

    def _sync(self, text):
        with patch(
            "workspace.calendar.services.ics_sync._fetch_feed", return_value=text
        ):
            sync_external_calendar(self.external)

    def _masters(self):
        return Event.objects.filter(
            calendar=self.calendar, recurrence_parent__isnull=True
        ).order_by("start")


class FeedSeriesTests(FeedTestCase):
    @expectedFailure  # #1623: the moved occurrence overwrites its series
    def test_a_moved_occurrence_keeps_its_series(self):
        self._sync(_feed(SERIES, MOVED))

        (master,) = self._masters()
        self.assertTrue(master.is_recurring)
        self.assertEqual(master.title, "Weekly sync")
        (moved,) = master.exceptions.all()
        self.assertEqual(moved.original_start, MOVED_FROM)
        self.assertEqual(moved.start, MOVED_TO)
        self.assertEqual(moved.title, "Weekly sync (moved)")
        self.assertEqual(occurrence_starts(self.calendar), OCCURRENCES)

    @expectedFailure  # #1623: the order of the VEVENTs decides which row wins
    def test_the_order_of_the_events_does_not_matter(self):
        self._sync(_feed(MOVED, SERIES))

        self.assertEqual(occurrence_starts(self.calendar), OCCURRENCES)

    @expectedFailure  # #1623: there is no exception row to drop
    def test_an_occurrence_the_feed_no_longer_moves_goes_back_to_the_series(self):
        self._sync(_feed(SERIES, MOVED))
        self.assertEqual(
            Event.objects.filter(recurrence_parent__isnull=False).count(), 1
        )
        self._sync(_feed(SERIES))

        (master,) = self._masters()
        self.assertFalse(master.exceptions.exists())
        self.assertIn(MOVED_FROM, occurrence_starts(self.calendar))

    @expectedFailure  # #1623: the series is a single event after the first sync
    def test_an_unchanged_feed_writes_nothing(self):
        self._sync(_feed(SERIES, MOVED))
        before = {e.uuid: e.updated_at for e in Event.objects.all()}

        self._sync(_feed(SERIES, MOVED))

        after = {e.uuid: e.updated_at for e in Event.objects.all()}
        self.assertEqual(len(after), 2)
        self.assertEqual(before, after)

    def test_a_series_that_leaves_the_feed_takes_its_exceptions(self):
        self._sync(_feed(SERIES, MOVED))
        self._sync(_feed(["UID:other@example.com", "DTSTART:20260401T100000Z"]))

        self.assertEqual(
            list(Event.objects.values_list("ical_uid", flat=True)),
            ["other@example.com"],
        )

    @expectedFailure  # #1623: an occurrence on its own replaces nothing yet
    def test_an_occurrence_without_its_series_is_a_single_event(self):
        self._sync(_feed(MOVED))

        (event,) = self._masters()
        self.assertFalse(event.is_recurring)
        self.assertEqual(event.start, MOVED_TO)
        self.assertIn("RECURRENCE-ID", event.ical_extra)


class FeedValueTests(FeedTestCase):
    def test_a_floating_time_is_the_owner_s_wall_clock(self):
        self._sync(_feed(["UID:f@example.com", "DTSTART:20260805T090000"]))

        (event,) = self._masters()
        self.assertEqual(event.start, datetime(2026, 8, 5, 7, tzinfo=UTC))
        self.assertEqual(event.timezone, "Europe/Paris")

    def test_an_all_day_value_is_its_own_day(self):
        self._sync(
            _feed(
                [
                    "UID:d@example.com",
                    "DTSTART;VALUE=DATE:20260805",
                    "DTEND;VALUE=DATE:20260806",
                ]
            )
        )

        (event,) = self._masters()
        self.assertTrue(event.all_day)
        self.assertEqual(event.start, datetime(2026, 8, 5, tzinfo=UTC))
        self.assertEqual(event.end, datetime(2026, 8, 6, tzinfo=UTC))
        self.assertEqual(event.timezone, "")

    @expectedFailure  # #1775: X-WR-TIMEZONE is ignored, the series recurs in UTC
    def test_x_wr_timezone_names_the_zone_a_utc_series_recurs_in(self):
        self._sync(
            _feed(
                [
                    "UID:w@example.com",
                    "DTSTART:20260323T080000Z",
                    "DTEND:20260323T090000Z",
                    "RRULE:FREQ=WEEKLY;COUNT=2",
                    "SUMMARY:Weekly",
                ],
                header=["X-WR-TIMEZONE:Europe/Paris"],
            )
        )

        (event,) = self._masters()
        self.assertEqual(event.timezone, "Europe/Paris")
        # 09:00 Paris both weeks, across the switch to summer time.
        self.assertEqual(
            occurrence_starts(self.calendar),
            [
                datetime(2026, 3, 23, 8, tzinfo=UTC),
                datetime(2026, 3, 30, 7, tzinfo=UTC),
            ],
        )

    @expectedFailure  # #1775: feed events drop every line without a column
    def test_alarms_attendees_and_x_properties_are_kept(self):
        self._sync(
            _feed(
                [
                    "UID:x@example.com",
                    "DTSTART:20260401T100000Z",
                    "ATTENDEE;CN=Bob:mailto:bob@test.com",
                    "X-MICROSOFT-CDO-BUSYSTATUS:BUSY",
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    "TRIGGER:-PT15M",
                    "END:VALARM",
                ]
            )
        )

        (event,) = self._masters()
        self.assertEqual(
            event.ical_extra.splitlines(),
            [
                "ATTENDEE;CN=Bob:mailto:bob@test.com",
                "X-MICROSOFT-CDO-BUSYSTATUS:BUSY",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "TRIGGER:-PT15M",
                "END:VALARM",
            ],
        )

    def test_a_todo_in_the_feed_is_skipped(self):
        text = _feed(["UID:e@example.com", "DTSTART:20260401T100000Z"]).replace(
            "END:VCALENDAR",
            "BEGIN:VTODO\r\nUID:t@example.com\r\nSUMMARY:Chore\r\nEND:VTODO\r\n"
            "END:VCALENDAR",
        )
        self._sync(text)

        self.assertEqual(
            list(Event.objects.values_list("ical_uid", flat=True)), ["e@example.com"]
        )


class FeedExportTests(FeedTestCase):
    """The real-world shapes, one producer each."""

    def _check(self, producer):
        self._sync(export(producer))

        series, offsite = self._masters()
        self.assertEqual(series.ical_uid, SERIES_UID[producer])
        self.assertEqual(series.title, "Weekly sync")
        self.assertEqual(series.timezone, "Europe/Paris")
        self.assertEqual(series.external_organizer, "alice@example.com")
        self.assertIn("ATTENDEE", series.ical_extra)
        self.assertIn("BEGIN:VALARM", series.ical_extra)
        (moved,) = series.exceptions.all()
        self.assertEqual(moved.original_start, MOVED_FROM)
        self.assertEqual(moved.title, "Weekly sync (moved)")
        self.assertEqual(occurrence_starts(self.calendar), OCCURRENCES)

        self.assertEqual(offsite.title, "Team offsite")
        self.assertTrue(offsite.all_day)
        self.assertEqual(offsite.start, datetime(2026, 4, 15, tzinfo=UTC))
        self.assertEqual(offsite.end, datetime(2026, 4, 16, tzinfo=UTC))

        # A second poll of the same export changes nothing.
        before = {e.uuid: e.updated_at for e in Event.objects.all()}
        self._sync(export(producer))
        self.assertEqual(before, {e.uuid: e.updated_at for e in Event.objects.all()})

    @expectedFailure  # #1623
    def test_google(self):
        self._check("google")

    @expectedFailure  # #1623, and the Windows zone ID resolves to nothing
    def test_outlook(self):
        self._check("outlook")

    @expectedFailure  # #1623
    def test_icloud(self):
        self._check("icloud")

    @expectedFailure  # #1623
    def test_nextcloud(self):
        self._check("nextcloud")

    @expectedFailure  # #1623
    def test_thunderbird(self):
        self._check("thunderbird")

    def test_every_producer_is_covered(self):
        self.assertEqual(
            {name for name in dir(self) if name.startswith("test_")}
            - {"test_every_producer_is_covered"},
            {f"test_{producer}" for producer in PRODUCERS},
        )

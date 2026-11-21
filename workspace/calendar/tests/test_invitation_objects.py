"""An emailed invitation is read object by object, and its METHOD decides the write."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.services.ics_processor import process_calendar_email
from workspace.users.services.settings import set_setting

from .ics_exports import (
    MOVED_FROM,
    MOVED_TO,
    OCCURRENCES,
    PRODUCERS,
    SERIES_UID,
    as_request,
    export,
    occurrence_starts,
)
from .test_ics_processor import ICSProcessorMixin

UID = "series@example.com"
ORGANIZER = "ORGANIZER;CN=Alice:mailto:alice@example.com"
SERIES = [
    f"UID:{UID}",
    ORGANIZER,
    "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@test.com",
    "SEQUENCE:0",
    "DTSTART;TZID=Europe/Paris:20260302T090000",
    "DTEND;TZID=Europe/Paris:20260302T100000",
    "RRULE:FREQ=WEEKLY;COUNT=8",
    "EXDATE;TZID=Europe/Paris:20260323T090000",
    "SUMMARY:Weekly sync",
]
MOVED = [
    f"UID:{UID}",
    ORGANIZER,
    "SEQUENCE:1",
    "RECURRENCE-ID;TZID=Europe/Paris:20260316T090000",
    "DTSTART;TZID=Europe/Paris:20260317T140000",
    "DTEND;TZID=Europe/Paris:20260317T150000",
    "SUMMARY:Weekly sync (moved)",
]


def _itip(method, *vevents):
    body = "".join(
        "BEGIN:VEVENT\r\n" + "".join(f"{line}\r\n" for line in lines) + "END:VEVENT\r\n"
        for lines in vevents
    )
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        f"METHOD:{method}\r\n{body}END:VCALENDAR\r\n"
    )


class InvitationTestCase(ICSProcessorMixin, TestCase):
    def setUp(self):
        super().setUp()
        for target, kwargs in (
            ("django_tz.now", {"return_value": datetime(2026, 2, 1, tzinfo=UTC)}),
            ("notify", {}),
        ):
            patcher = patch(
                f"workspace.calendar.services.ics_processor.{target}", **kwargs
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        self._next_uid = 0

    def tearDown(self):
        cache.clear()

    def _receive(self, text):
        self._next_uid += 1
        process_calendar_email(self._create_mail_with_ics(text, uid=self._next_uid))

    def _calendar(self):
        return Calendar.objects.get(mail_account=self.account)

    def _masters(self):
        return Event.objects.filter(recurrence_parent__isnull=True).order_by("start")


class RecurringInvitationTests(InvitationTestCase):
    def test_a_recurring_invitation_is_a_series(self):
        self._receive(_itip("REQUEST", SERIES, MOVED))

        (master,) = self._masters()
        self.assertTrue(master.is_recurring)
        self.assertEqual(master.ical_uid, UID)
        (moved,) = master.exceptions.all()
        self.assertEqual(moved.original_start, MOVED_FROM)
        self.assertEqual(moved.start, MOVED_TO)
        self.assertEqual(occurrence_starts(self._calendar()), OCCURRENCES)

    def test_an_update_of_one_occurrence_leaves_the_series_alone(self):
        self._receive(_itip("REQUEST", SERIES))
        self._receive(_itip("REQUEST", MOVED))

        (master,) = self._masters()
        self.assertEqual(master.title, "Weekly sync")
        self.assertTrue(master.is_recurring)
        (moved,) = master.exceptions.all()
        self.assertEqual(moved.start, MOVED_TO)
        self.assertEqual(occurrence_starts(self._calendar()), OCCURRENCES)

    def test_a_newer_request_replaces_the_series_and_its_exceptions(self):
        self._receive(_itip("REQUEST", SERIES, MOVED))
        self.assertEqual(
            Event.objects.filter(recurrence_parent__isnull=False).count(), 1
        )
        newer = [
            line.replace("SEQUENCE:0", "SEQUENCE:2").replace(
                "SUMMARY:Weekly sync", "SUMMARY:Weekly sync v2"
            )
            for line in SERIES
        ]
        self._receive(_itip("REQUEST", newer))

        (master,) = self._masters()
        self.assertEqual(master.title, "Weekly sync v2")
        self.assertFalse(master.exceptions.exists())

    def test_an_older_request_changes_nothing(self):
        self._receive(_itip("REQUEST", SERIES, MOVED))
        self._receive(_itip("REQUEST", SERIES))

        (master,) = self._masters()
        self.assertEqual(master.exceptions.count(), 1)

    def test_the_recipient_is_invited_once_per_series(self):
        self._receive(_itip("REQUEST", SERIES, MOVED))

        self.assertEqual(
            EventMember.objects.filter(user=self.user).count(),
            1,
        )


class CancelTests(InvitationTestCase):
    def test_cancelling_one_occurrence_cancels_that_occurrence_only(self):
        self._receive(_itip("REQUEST", SERIES))
        self._receive(
            _itip(
                "CANCEL",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "SEQUENCE:1",
                    "RECURRENCE-ID;TZID=Europe/Paris:20260309T090000",
                    "DTSTART;TZID=Europe/Paris:20260309T090000",
                    "STATUS:CANCELLED",
                ],
            )
        )

        (master,) = self._masters()
        self.assertFalse(master.is_cancelled)
        starts = occurrence_starts(self._calendar())
        self.assertNotIn(datetime(2026, 3, 9, 8, tzinfo=UTC), starts)
        self.assertIn(datetime(2026, 3, 2, 8, tzinfo=UTC), starts)
        self.assertIn(datetime(2026, 3, 30, 7, tzinfo=UTC), starts)

    def test_cancelling_a_moved_occurrence_cancels_its_exception(self):
        self._receive(_itip("REQUEST", SERIES, MOVED))
        self._receive(
            _itip(
                "CANCEL",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "SEQUENCE:2",
                    "RECURRENCE-ID;TZID=Europe/Paris:20260316T090000",
                    "DTSTART;TZID=Europe/Paris:20260317T140000",
                ],
            )
        )

        self.assertNotIn(MOVED_TO, occurrence_starts(self._calendar()))
        (master,) = self._masters()
        self.assertFalse(master.is_cancelled)

    def test_cancelling_the_series_cancels_it(self):
        self._receive(_itip("REQUEST", SERIES))
        self._receive(_itip("CANCEL", [f"UID:{UID}", ORGANIZER, "SEQUENCE:1"]))

        (master,) = self._masters()
        self.assertTrue(master.is_cancelled)


class MethodTests(InvitationTestCase):
    def test_a_reply_never_creates_an_event(self):
        self._receive(
            _itip(
                "REPLY",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "ATTENDEE;PARTSTAT=ACCEPTED:mailto:carol@example.com",
                    "DTSTART:20260302T080000Z",
                    "SUMMARY:Weekly sync",
                ],
            )
        )

        self.assertFalse(Event.objects.exists())

    def test_a_reply_leaves_an_existing_invitation_alone(self):
        self._receive(_itip("REQUEST", SERIES))
        self._receive(
            _itip(
                "REPLY",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "SEQUENCE:5",
                    "ATTENDEE;PARTSTAT=DECLINED:mailto:carol@example.com",
                    "DTSTART:20260302T080000Z",
                    "SUMMARY:Something else",
                ],
            )
        )

        (master,) = self._masters()
        self.assertEqual(master.title, "Weekly sync")
        self.assertEqual(master.ical_sequence, 0)

    def test_other_methods_are_ignored(self):
        for method in ("PUBLISH", "COUNTER", "REFRESH", "ADD"):
            with self.subTest(method):
                self._receive(_itip(method, SERIES))
                self.assertFalse(Event.objects.exists())


class InvitationValueTests(InvitationTestCase):
    def test_a_floating_time_is_the_recipient_s_wall_clock(self):
        self._receive(
            _itip("REQUEST", [f"UID:{UID}", ORGANIZER, "DTSTART:20260805T090000"])
        )

        (event,) = self._masters()
        self.assertEqual(event.start, datetime(2026, 8, 5, 7, tzinfo=UTC))
        self.assertEqual(event.timezone, "Europe/Paris")

    def test_an_all_day_value_is_its_own_day(self):
        self._receive(
            _itip(
                "REQUEST",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "DTSTART;VALUE=DATE:20260805",
                    "DTEND;VALUE=DATE:20260806",
                ],
            )
        )

        (event,) = self._masters()
        self.assertTrue(event.all_day)
        self.assertEqual(event.start, datetime(2026, 8, 5, tzinfo=UTC))
        self.assertEqual(event.end, datetime(2026, 8, 6, tzinfo=UTC))

    def test_attendees_and_x_properties_are_kept_but_not_the_sender_s_alarms(
        self,
    ):
        self._receive(
            _itip(
                "REQUEST",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "DTSTART:20260401T100000Z",
                    "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@test.com",
                    "X-MICROSOFT-CDO-BUSYSTATUS:BUSY",
                    "BEGIN:VALARM",
                    "ACTION:AUDIO",
                    "ATTACH:https://sender.example/ping.mp3",
                    "TRIGGER:-PT15M",
                    "END:VALARM",
                    "CATEGORIES:Work",
                ],
            )
        )

        (event,) = self._masters()
        self.assertEqual(
            event.ical_extra.splitlines(),
            [
                ORGANIZER,
                "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@test.com",
                "X-MICROSOFT-CDO-BUSYSTATUS:BUSY",
                "CATEGORIES:Work",
            ],
        )

    def test_an_end_closing_another_component_creates_nothing(self):
        # Kept verbatim, the END:VEVENT inside the alarm would close the event
        # wherever it is served, and the sender's next lines would be events.
        self._receive(
            _itip(
                "REQUEST",
                [
                    f"UID:{UID}",
                    ORGANIZER,
                    "DTSTART:20260401T100000Z",
                    "BEGIN:VALARM",
                    "TRIGGER:-PT15M",
                    "END:VEVENT",
                    "BEGIN:VEVENT",
                    "UID:smuggled@evil.test",
                    "DTSTART:20260401T100000Z",
                    "SUMMARY:Smuggled",
                    "END:VALARM",
                ],
            )
        )

        self.assertFalse(Event.objects.exists())


class InvitationExportTests(InvitationTestCase):
    """The real-world shapes, sent as invitations."""

    def _check(self, producer):
        self._receive(as_request(export(producer)))

        # The all-day event of the export names no organizer: no invitation.
        (series,) = self._masters()
        self.assertEqual(series.ical_uid, SERIES_UID[producer])
        self.assertEqual(series.timezone, "Europe/Paris")
        self.assertEqual(series.external_organizer, "alice@example.com")
        self.assertIn("ATTENDEE", series.ical_extra)
        self.assertNotIn("VALARM", series.ical_extra)
        (moved,) = series.exceptions.all()
        self.assertEqual(moved.original_start, MOVED_FROM)
        self.assertEqual(occurrence_starts(self._calendar()), OCCURRENCES)

    def test_google(self):
        self._check("google")

    def test_outlook(self):
        self._check("outlook")

    def test_icloud(self):
        self._check("icloud")

    def test_nextcloud(self):
        self._check("nextcloud")

    def test_thunderbird(self):
        self._check("thunderbird")

    def test_every_producer_is_covered(self):
        self.assertEqual(
            {name for name in dir(self) if name.startswith("test_")}
            - {"test_every_producer_is_covered"},
            {f"test_{producer}" for producer in PRODUCERS},
        )

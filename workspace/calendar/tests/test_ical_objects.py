from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from workspace.calendar.models import Event
from workspace.calendar.services.ical_objects import (
    InvalidObject,
    _fold,
    parse_object,
    render_calendar,
    render_object,
)

PARIS = ZoneInfo("Europe/Paris")


def _ics(*vevents, extra_components=()):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN"]
    for component in extra_components:
        lines.extend(component)
    for vevent in vevents:
        lines.extend(["BEGIN:VEVENT", *vevent, "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


SERIES = [
    "UID:series-1",
    "DTSTAMP:20261001T080000Z",
    "DTSTART;TZID=Europe/Paris:20261005T090000",
    "DTEND;TZID=Europe/Paris:20261005T100000",
    "SUMMARY:Standup\\, daily",
    "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10",
    "EXDATE;TZID=Europe/Paris:20261007T090000",
    "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC",
    'ATTENDEE;CN="Bob, Jr";PARTSTAT=ACCEPTED:mailto:bob@example.com',
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    "TRIGGER:-PT15M",
    "DESCRIPTION:Reminder",
    "END:VALARM",
]
OVERRIDE = [
    "UID:series-1",
    "DTSTAMP:20261001T080000Z",
    "RECURRENCE-ID;TZID=Europe/Paris:20261012T090000",
    "DTSTART;TZID=Europe/Paris:20261012T110000",
    "DTEND;TZID=Europe/Paris:20261012T120000",
    "SUMMARY:Standup (moved)",
]


class ParseObjectTests(SimpleTestCase):
    def test_series_with_an_override(self):
        parsed = parse_object(_ics(SERIES, OVERRIDE))

        self.assertEqual(parsed.uid, "series-1")
        master = parsed.master
        self.assertEqual(master.title, "Standup, daily")
        self.assertEqual(master.start, datetime(2026, 10, 5, 7, tzinfo=UTC))
        self.assertEqual(master.end, datetime(2026, 10, 5, 8, tzinfo=UTC))
        self.assertEqual(master.timezone, "Europe/Paris")
        self.assertFalse(master.all_day)
        self.assertEqual(
            master.rule,
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10\n"
            "EXDATE;TZID=Europe/Paris:20261007T090000",
        )

        (override,) = parsed.overrides
        self.assertEqual(override.recurrence_id, datetime(2026, 10, 12, 7, tzinfo=UTC))
        self.assertEqual(override.start, datetime(2026, 10, 12, 9, tzinfo=UTC))
        self.assertEqual(override.title, "Standup (moved)")

    def test_unmapped_lines_are_kept_verbatim(self):
        extra = parse_object(_ics(SERIES)).master.extra.splitlines()
        self.assertEqual(
            extra,
            [
                "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC",
                'ATTENDEE;CN="Bob, Jr";PARTSTAT=ACCEPTED:mailto:bob@example.com',
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "TRIGGER:-PT15M",
                "DESCRIPTION:Reminder",
                "END:VALARM",
            ],
        )

    def test_folded_lines_are_unfolded(self):
        vevent = [
            "UID:fold",
            "DTSTART:20261005T090000Z",
            "SUMMARY:a long",
            "  title",
            "X-NOTE:first",
            " second",
        ]
        parsed = parse_object(_ics(vevent))
        self.assertEqual(parsed.master.title, "a long title")
        self.assertEqual(parsed.master.extra, "X-NOTE:firstsecond")

    def test_all_day_event(self):
        vevent = [
            "UID:day",
            "DTSTART;VALUE=DATE:20261005",
            "DTEND;VALUE=DATE:20261006",
            "SUMMARY:Holiday",
        ]
        master = parse_object(_ics(vevent), PARIS).master
        self.assertTrue(master.all_day)
        self.assertEqual(master.start, datetime(2026, 10, 5, tzinfo=UTC))
        self.assertEqual(master.end, datetime(2026, 10, 6, tzinfo=UTC))
        self.assertEqual(master.timezone, "")

    def test_floating_time_is_read_in_the_default_zone(self):
        vevent = ["UID:float", "DTSTART:20261005T090000", "SUMMARY:x"]
        master = parse_object(_ics(vevent), PARIS).master
        self.assertEqual(master.start, datetime(2026, 10, 5, 7, tzinfo=UTC))
        self.assertEqual(master.timezone, "Europe/Paris")

    def test_duration_sets_the_end(self):
        vevent = ["UID:dur", "DTSTART:20261005T090000Z", "DURATION:PT90M"]
        master = parse_object(_ics(vevent)).master
        self.assertEqual(master.end, datetime(2026, 10, 5, 10, 30, tzinfo=UTC))

    def test_cancelled_override(self):
        cancelled = [*OVERRIDE, "STATUS:CANCELLED"]
        (override,) = parse_object(_ics(SERIES, cancelled)).overrides
        self.assertTrue(override.cancelled)
        self.assertNotIn("STATUS", override.extra)

    def test_other_status_values_stay_extra(self):
        vevent = ["UID:s", "DTSTART:20261005T090000Z", "STATUS:TENTATIVE"]
        master = parse_object(_ics(vevent)).master
        self.assertFalse(master.cancelled)
        self.assertEqual(master.extra, "STATUS:TENTATIVE")

    def test_lone_override_becomes_a_plain_event(self):
        parsed = parse_object(_ics(OVERRIDE))
        self.assertIsNone(parsed.master.recurrence_id)
        self.assertEqual(parsed.overrides, [])
        self.assertIn(
            "RECURRENCE-ID;TZID=Europe/Paris:20261012T090000", parsed.master.extra
        )

    def test_todo_is_refused(self):
        todo = ["BEGIN:VTODO", "UID:t", "SUMMARY:x", "END:VTODO"]
        with self.assertRaises(InvalidObject) as ctx:
            parse_object(_ics(extra_components=[todo]))
        self.assertEqual(ctx.exception.condition, "supported-calendar-component")

    def test_mixed_uids_are_refused(self):
        other = [line.replace("series-1", "series-2") for line in OVERRIDE]
        with self.assertRaises(InvalidObject):
            parse_object(_ics(SERIES, other))

    def test_two_masters_are_refused(self):
        with self.assertRaises(InvalidObject):
            parse_object(_ics(SERIES, SERIES))

    def test_missing_dtstart_is_refused(self):
        with self.assertRaises(InvalidObject):
            parse_object(_ics(["UID:x", "SUMMARY:no start"]))

    def test_broken_date_is_refused(self):
        with self.assertRaises(InvalidObject):
            parse_object(_ics(["UID:x", "DTSTART:2026XX"]))

    def test_garbage_is_refused(self):
        for body in (b"\xff\xfe", "hello", _ics()):
            with self.subTest(body=body), self.assertRaises(InvalidObject):
                parse_object(body)


class RenderObjectTests(SimpleTestCase):
    def _series(self, **fields):
        defaults = {
            "title": "Standup",
            "start": datetime(2026, 10, 5, 7, tzinfo=UTC),
            "end": datetime(2026, 10, 5, 8, tzinfo=UTC),
            "timezone": "Europe/Paris",
            "recurrence_rule": "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10",
            "ical_uid": "series-1",
        }
        return Event(**(defaults | fields))

    def test_zoned_event_carries_its_vtimezone(self):
        text = render_object(self._series())
        self.assertIn("DTSTART;TZID=Europe/Paris:20261005T090000", text)
        self.assertIn("BEGIN:VTIMEZONE\r\nTZID:Europe/Paris", text)
        self.assertIn("UID:series-1", text)

    def test_native_event_is_served_under_its_uuid(self):
        event = self._series(ical_uid=None)
        self.assertIn(f"UID:{event.uuid}", render_object(event))

    def test_rule_and_extra_lines_are_emitted_verbatim(self):
        extra = "X-A:1\nBEGIN:VALARM\nTRIGGER:-PT5M\nACTION:DISPLAY\nEND:VALARM"
        text = render_object(self._series(ical_extra=extra))
        self.assertIn(
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10\r\nX-A:1\r\nBEGIN:VALARM\r\n"
            "TRIGGER:-PT5M\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VEVENT\r\n",
            text,
        )

    def test_extra_lines_name_their_zones(self):
        extra = "X-ORIGINAL;TZID=America/New_York:20261005T090000"
        text = render_object(self._series(timezone="", ical_extra=extra))
        self.assertIn("TZID:America/New_York", text)

    def test_all_day_event(self):
        event = self._series(
            all_day=True,
            timezone="",
            recurrence_rule="",
            start=datetime(2026, 10, 5, tzinfo=UTC),
            end=datetime(2026, 10, 6, tzinfo=UTC),
        )
        text = render_object(event)
        self.assertIn("DTSTART;VALUE=DATE:20261005", text)
        self.assertNotIn("VTIMEZONE", text)

    def test_exceptions(self):
        master = self._series()
        moved = Event(
            title="Moved",
            start=datetime(2026, 10, 12, 9, tzinfo=UTC),
            end=datetime(2026, 10, 12, 10, tzinfo=UTC),
            original_start=datetime(2026, 10, 12, 7, tzinfo=UTC),
        )
        dropped = Event(
            title="Standup",
            start=datetime(2026, 10, 14, 7, tzinfo=UTC),
            original_start=datetime(2026, 10, 14, 7, tzinfo=UTC),
            is_cancelled=True,
        )
        text = render_object(master, [moved, dropped])
        self.assertEqual(text.count("BEGIN:VEVENT"), 2)
        self.assertIn("RECURRENCE-ID;TZID=Europe/Paris:20261012T090000", text)
        self.assertIn("EXDATE;TZID=Europe/Paris:20261014T090000", text)

    def test_render_parse_round_trip(self):
        extra = 'ATTENDEE;CN="Bob, Jr":mailto:bob@example.com\nX-A:1'
        master = self._series(
            description="Line 1\nLine 2, with comma", ical_extra=extra
        )
        parsed = parse_object(render_object(master))
        self.assertEqual(parsed.master.title, "Standup")
        self.assertEqual(parsed.master.description, "Line 1\nLine 2, with comma")
        self.assertEqual(parsed.master.start, master.start)
        self.assertEqual(parsed.master.timezone, "Europe/Paris")
        self.assertEqual(parsed.master.rule, master.recurrence_rule)
        self.assertEqual(parsed.master.extra, extra)

    def test_external_organizer_is_served(self):
        text = render_object(self._series(external_organizer="boss@example.com"))
        self.assertIn("ORGANIZER:mailto:boss@example.com", text)

    def test_feed_names_the_calendar(self):
        text = render_calendar("Team, shared", [(self._series(), [])])
        self.assertIn("X-WR-CALNAME:Team\\, shared", text)
        self.assertEqual(text.count("BEGIN:VEVENT"), 1)


class FoldTests(SimpleTestCase):
    def test_short_line_is_untouched(self):
        self.assertEqual(_fold("SUMMARY:x"), "SUMMARY:x\r\n")

    def test_long_line_is_folded_without_splitting_a_character(self):
        line = "SUMMARY:" + "é" * 80
        folded = _fold(line)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode()), 75)
            part.encode().decode()  # whole characters only
        self.assertEqual(folded.replace("\r\n ", "").removesuffix("\r\n"), line)

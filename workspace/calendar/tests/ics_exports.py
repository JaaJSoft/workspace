"""Calendar exports in the shape each producer writes, and what they hold.

Every file under ``ics/`` holds the same calendar: a weekly series on Monday
09:00-10:00 Europe/Paris (eight occurrences from 2026-03-02, crossing the
switch to summer time), its 16 March occurrence moved to Tuesday 17 at 14:00,
its 23 March occurrence cancelled with an EXDATE, an alarm, attendees and the
producer's own X- properties; plus an all-day event on 15 April. Each one
keeps its producer's quirks: Google lists the moved occurrence before its
series, Outlook names its zone with a Windows ID, Thunderbird writes its
EXDATE in UTC.
"""

from datetime import UTC, datetime
from pathlib import Path

from workspace.calendar.models import Event
from workspace.calendar.recurrence import expand_recurring_events

EXPORTS = Path(__file__).resolve().parent / "ics"

PRODUCERS = ("google", "outlook", "icloud", "nextcloud", "thunderbird")

SERIES_UID = {
    "google": "5r2b8k1v0q3l6m9n4p7s2t5u8w@google.com",
    "outlook": (
        "040000008200E00074C5B7101A82E00800000000D0A1C3F2B4A5DC0100000000000000"
        "0010000000A1B2C3D4E5F60718293A4B5C6D7E8F90"
    ),
    "icloud": "8D4A1B52-3C6E-4F7A-9B0D-2E1F3A4B5C6D",
    "nextcloud": "3f1c2b7e-9d4a-4e5b-8c6f-1a2b3c4d5e6f",
    "thunderbird": "b2d6c1e0-7f3a-4c58-9e21-5d4f6a7b8c9d",
}

MOVED_FROM = datetime(2026, 3, 16, 8, tzinfo=UTC)
MOVED_TO = datetime(2026, 3, 17, 13, tzinfo=UTC)

# The series as a reader sees it, in UTC: 08:00 in winter, 07:00 in summer,
# the moved occurrence on the Tuesday and the cancelled one gone.
OCCURRENCES = [
    datetime(2026, 3, 2, 8, tzinfo=UTC),
    datetime(2026, 3, 9, 8, tzinfo=UTC),
    MOVED_TO,
    datetime(2026, 3, 30, 7, tzinfo=UTC),
    datetime(2026, 4, 6, 7, tzinfo=UTC),
    datetime(2026, 4, 13, 7, tzinfo=UTC),
    datetime(2026, 4, 20, 7, tzinfo=UTC),
]


def export(producer):
    return (EXPORTS / f"{producer}.ics").read_text(encoding="utf-8")


def as_request(text):
    """The export sent as an iTIP invitation."""
    if "METHOD:" in text:
        return text.replace("METHOD:PUBLISH", "METHOD:REQUEST")
    return text.replace("VERSION:2.0\r\n", "VERSION:2.0\r\nMETHOD:REQUEST\r\n", 1)


def occurrence_starts(calendar):
    """Every occurrence start of *calendar*'s series in March and April 2026."""
    masters = Event.objects.filter(
        calendar=calendar, recurrence_parent__isnull=True, is_recurring=True
    )
    occurrences = expand_recurring_events(
        masters, datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC)
    )
    return sorted(datetime.fromisoformat(occ["start"]) for occ in occurrences)

"""Timezone semantics for calendar events.

Storage contract:

- ``Event.start`` / ``Event.end`` are aware UTC datetimes, always.
- All-day events are UTC-midnight anchored: the stored UTC date IS the
  calendar-day label, independent of any timezone. ``normalize_all_day``
  enforces that invariant at every write boundary.
- ``Event.timezone`` (IANA name, blank = UTC) records the wall-clock zone
  a recurring series is anchored in; expansion preserves the local time
  across DST transitions. An empty/UTC/invalid value selects the legacy
  fixed-step UTC expansion.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone as dj_timezone


def normalize_all_day(dt):
    """Truncate a datetime to the UTC midnight of its UTC date (or None)."""
    if dt is None:
        return None
    dt = dt.astimezone(UTC)
    return datetime(dt.year, dt.month, dt.day, tzinfo=UTC)


def all_day_label(day: date) -> datetime:
    """The stored value of an all-day event on *day*: UTC midnight of it.

    Comparing all-day rows against someone's local day goes through this,
    never through that day's local bounds: an all-day event on Feb 1 is
    stored at 00:00 UTC whatever the reader's zone, so the instants of the
    reader's Feb 1 (23:00 UTC the day before in Paris) are the wrong window.
    """
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def event_timezone(event):
    """Return the event's ZoneInfo, or None for the legacy UTC path."""
    name = event.timezone
    if not name or name == "UTC":
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, KeyError, ValueError:
        return None


def current_timezone_name():
    """IANA name of the active timezone, or '' when it is plain UTC."""
    name = dj_timezone.get_current_timezone_name()
    return "" if name == "UTC" else name

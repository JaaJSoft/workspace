"""Datetime parsing for values typed by a user or a model.

Naive strings ("2026-03-21T09:00") are the common case at those boundaries:
nobody types an offset. Interpreting them as UTC silently books meetings in
the wrong hour, so they are anchored in the caller's timezone instead.
"""

from datetime import datetime


def parse_local_datetime(value: str, tz):
    """Parse an ISO 8601 datetime, interpreting naive values in *tz*.

    Returns ``None`` when the string cannot be parsed, so callers can report
    the bad input rather than raise.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt

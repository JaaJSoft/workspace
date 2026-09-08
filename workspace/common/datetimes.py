"""Datetime parsing for values typed by a user or a model.

Naive strings ("2026-03-21T09:00") are the common case at those boundaries:
nobody types an offset. Interpreting them as UTC silently books meetings in
the wrong hour, so they are anchored in the caller's timezone instead.
"""

from datetime import date, datetime, time, timedelta

from django.utils import timezone


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


def local_date_range(date_from: date, date_to: date, tz=None):
    """Return the aware ``[start, end)`` bounds spanning the inclusive local dates.

    Filtering with ``col__gte=start, col__lt=end`` selects the same rows as
    ``col__date__gte=date_from, col__date__lte=date_to`` but keeps the column
    bare, so the database can use an index on it; the ``__date`` lookup wraps
    the column in a timezone cast that no index matches. Days are taken in
    *tz*, the active timezone by default, which is also what ``__date`` and
    ``TruncDate`` use.
    """
    if tz is None:
        tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min), tz
    )
    return start, end

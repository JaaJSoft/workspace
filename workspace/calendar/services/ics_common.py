"""Shared ICS property parsing for the invitation and feed-sync importers.

RFC 5545 semantics enforced here:

- DATE values are all-day day labels -> UTC midnight, no zone.
- DATE-TIME with TZID (or any aware tzinfo) -> converted to UTC for
  storage, with the IANA key returned so the event records the wall-clock
  zone it was authored in.
- Naive (floating) DATE-TIME means "local time of the observer" -> it is
  interpreted in *default_tz* (the calendar owner's zone), not UTC.
"""

from datetime import UTC, datetime


def parse_dt_prop(dt_prop, default_tz=None):
    """Convert an icalendar DATE/DATE-TIME property to (aware UTC dt, tzid)."""
    if dt_prop is None:
        return None, ""
    dt = dt_prop.dt
    if not hasattr(dt, "hour"):
        return datetime(dt.year, dt.month, dt.day, tzinfo=UTC), ""
    if dt.tzinfo is None:
        if default_tz is not None:
            return dt.replace(tzinfo=default_tz).astimezone(UTC), _tzid_of(default_tz)
        return dt.replace(tzinfo=UTC), ""
    tzid = _tzid_of(dt.tzinfo)
    return dt.astimezone(UTC), tzid


def _tzid_of(tzinfo):
    """IANA key of a tzinfo, or '' for UTC/offset-only zones."""
    key = getattr(tzinfo, "key", None) or getattr(tzinfo, "zone", None)
    if not key or key == "UTC":
        return ""
    return key


def is_all_day(dt_prop):
    """Return True if the property is a DATE (all-day), not a DATE-TIME."""
    if dt_prop is None:
        return False
    return not hasattr(dt_prop.dt, "hour")


def extract_email(address_prop):
    """Extract the email address from an ORGANIZER/ATTENDEE property."""
    if not address_prop:
        return ""
    value = str(address_prop)
    if value.lower().startswith("mailto:"):
        return value[7:]
    return value

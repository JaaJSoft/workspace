"""Read-only ICS feed of a calendar, behind a secret URL.

The URL is the credential: whoever holds it can read the calendar, which is
what lets any calendar client on earth subscribe without an account. Turning
the feed off or rotating the token is how a leaked URL gets revoked.
"""

import secrets

from django.urls import reverse

from .calendar_objects import calendar_masters
from .ical_objects import render_calendar
from .sync_log import RENDER_VERSION


def enable_feed(calendar):
    """Give *calendar* a fresh feed token, revoking any previous URL."""
    calendar.feed_token = secrets.token_urlsafe(32)
    calendar.save(update_fields=["feed_token", "updated_at"])
    return calendar.feed_token


def disable_feed(calendar):
    calendar.feed_token = None
    calendar.save(update_fields=["feed_token", "updated_at"])


def feed_path(calendar):
    """Path of the calendar's feed, or None while the feed is off."""
    if not calendar.feed_token:
        return None
    return reverse("calendar-feed", kwargs={"token": calendar.feed_token})


def feed_etag(calendar):
    """Changes whenever an event of the calendar or the calendar itself does."""
    return f'"{calendar.sync_revision}-{int(calendar.updated_at.timestamp())}-{RENDER_VERSION}"'


def render_feed(calendar):
    masters = calendar_masters(calendar)
    return render_calendar(
        calendar.name, ((master, list(master.exceptions.all())) for master in masters)
    )

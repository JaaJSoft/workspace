"""Fetch and sync external ICS calendar feeds."""

import logging

import httpx2
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from workspace.calendar.models import Event
from workspace.calendar.services.calendar_objects import (
    object_unchanged,
    save_object,
)
from workspace.calendar.services.ical_objects import (
    dated,
    is_dated,
    read_calendar,
    standalone,
)
from workspace.common.url_guard import (
    ResponseTooLarge,
    UnsafeUrl,
    check_remote_url,
    guarded_transport,
    read_capped,
)
from workspace.users.services.settings import get_user_timezone

logger = logging.getLogger(__name__)

# A feed holding years of a busy calendar stays well under this.
MAX_FEED_BYTES = 20 * 1024 * 1024
MAX_FEED_REDIRECTS = 5


@transaction.atomic
def sync_external_calendar(external_calendar):
    """Fetch an ICS feed and sync its objects into the linked Calendar.

    - Uses ETag/If-None-Match to skip unchanged feeds.
    - Writes each object - a series and its exceptions, or a single event -
      keyed by UID, and leaves an object the feed did not change untouched.
    - Deletes the objects whose UID is no longer in the feed.
    """
    ics_text = _fetch_feed(external_calendar)

    if ics_text is None:
        # 304 Not Modified — just update timestamp
        external_calendar.last_synced_at = timezone.now()
        external_calendar.save(update_fields=["last_synced_at"])
        return

    # Scheduled polls are serialized by their claim, manual syncs are not: two
    # runs reading the same exceptions would each add the same new one.
    list(
        type(external_calendar)
        .objects.select_for_update()
        .filter(pk=external_calendar.pk)
        .values_list("pk", flat=True)
    )
    calendar = external_calendar.calendar
    # Floating (naive) feed times mean "local time of the observer": the
    # closest observer we have is the calendar owner.
    feed = read_calendar(ics_text, get_user_timezone(calendar.owner))

    # Feeds that don't support ETag/If-None-Match return 200 on every poll:
    # without comparing first, every row would be rewritten (and its
    # ``updated_at`` advanced) every 15 minutes with nothing changed upstream.
    existing_by_uid = {
        master.ical_uid: master
        for master in Event.objects.filter(
            calendar=calendar, recurrence_parent__isnull=True, ical_uid__isnull=False
        )
        .exclude(ical_uid="")
        .prefetch_related("exceptions")
    }

    # An event the feed lists but that could not be read this time is still
    # in the feed: only the ones that left it are deleted.
    seen_uids = set(feed.unreadable)
    for obj in feed.objects:
        obj = standalone(obj)
        seen_uids.add(obj.uid)
        master = existing_by_uid.get(obj.uid)
        if master is not None and (obj.uid in feed.unreadable or not is_dated(obj)):
            # Rewriting it from what could be read would drop the rest, the
            # exceptions included: keep what is stored until the feed mends.
            continue
        obj = dated(obj)
        if obj is None:
            continue
        organizer = obj.master.organizer
        if master is None:
            _create_object(calendar, obj, organizer)
        elif not object_unchanged(master, obj, external_organizer=organizer):
            save_object(master, obj, external_organizer=organizer)

    # Remove the objects that disappeared from the feed, exceptions included.
    Event.objects.filter(
        calendar=calendar,
        recurrence_parent__isnull=True,
        ical_uid__isnull=False,
    ).exclude(
        ical_uid__in=seen_uids,
    ).exclude(
        ical_uid="",
    ).delete()

    external_calendar.last_synced_at = timezone.now()
    external_calendar.last_error = ""
    external_calendar.save(update_fields=["last_synced_at", "last_etag", "last_error"])


def _create_object(calendar, obj, organizer):
    master = Event(calendar=calendar, owner=calendar.owner, ical_uid=obj.uid)
    try:
        with transaction.atomic():
            save_object(master, obj, external_organizer=organizer)
    except IntegrityError:
        # A concurrent sync of the same feed inserted this UID first (the
        # unique constraint on calendar and UID): write over its row instead.
        master = Event.objects.get(
            calendar=calendar, ical_uid=obj.uid, recurrence_parent__isnull=True
        )
        save_object(master, obj, external_organizer=organizer)


def _fetch_feed(external_calendar):
    """Fetch the ICS feed, returning the text or None on 304.

    The URL is the user's, so the connection only goes to a public address,
    on every redirect hop too, and the body is capped.
    """
    check_remote_url(external_calendar.url)
    headers = {}
    if external_calendar.last_etag:
        headers["If-None-Match"] = external_calendar.last_etag

    with (
        httpx2.Client(
            transport=guarded_transport(),
            timeout=30,
            follow_redirects=True,
            max_redirects=MAX_FEED_REDIRECTS,
        ) as client,
        client.stream("GET", external_calendar.url, headers=headers) as resp,
    ):
        if resp.status_code == 304:
            return None
        resp.raise_for_status()
        body = read_capped(resp, MAX_FEED_BYTES)
        encoding = resp.encoding or "utf-8"

    external_calendar.last_etag = resp.headers.get("ETag", "")
    return body.decode(encoding, errors="replace")


def sync_error_message(exc):
    """What ``last_error`` shows the owner for a failed sync.

    Built from the kind of failure, never from the exception's text: that
    text quotes the remote's answer and the addresses tried, which is what a
    probe of the server's network would read back through the API.
    """
    if isinstance(exc, UnsafeUrl):
        return exc.user_message
    if isinstance(exc, ResponseTooLarge):
        return _("The feed is larger than %(size)d MB.") % {
            "size": exc.max_bytes // (1024 * 1024)
        }
    if isinstance(exc, httpx2.HTTPStatusError):
        return _("The feed server answered with HTTP status %(status)d.") % {
            "status": exc.response.status_code
        }
    if isinstance(exc, httpx2.TooManyRedirects):
        return _("The feed redirects too many times.")
    if isinstance(exc, httpx2.TimeoutException):
        return _("The feed server did not answer in time.")
    if isinstance(exc, httpx2.HTTPError):
        return _("The feed server could not be reached.")
    if isinstance(exc, ValueError):
        return _("The feed is not a valid iCalendar file.")
    return _("Unexpected error, see the server logs.")


def external_calendars_with_errors():
    """Active external calendars whose last sync recorded an error."""
    from workspace.calendar.models_external import ExternalCalendar

    return ExternalCalendar.objects.filter(is_active=True).exclude(last_error="")


def queue_external_calendar_syncs(externals):
    """Queue a background sync for the active feeds in *externals*.

    Returns the number of syncs queued. Manual dispatches carry no claim
    token (see ``calendar.sync_external_calendar``).
    """
    from workspace.calendar.tasks import sync_external_calendar_task

    count = 0
    for ext in externals.filter(is_active=True):
        sync_external_calendar_task.delay(str(ext.uuid))
        count += 1
    return count


def clear_sync_errors(externals):
    """Blank ``last_error`` on *externals*; returns the number of rows updated."""
    return externals.exclude(last_error="").update(last_error="")

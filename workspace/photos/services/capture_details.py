"""Correcting when and where photos and videos were taken, by hand, in bulk.

A camera set to the wrong clock, or one left on the home timezone during a
trip, scatters a whole batch across the timeline; a scanned print has no date
at all. The corrections land on the MediaItem row, which everyone who can open
the photo sees, and flag it (``taken_at_by_user``, ``location_by_user``) so
that reading the file again or renaming it never undoes them. The file's bytes
are left untouched. Restoring hands the fields back to the file: the flags
drop and the file is read again.
"""

from datetime import UTC, date, datetime, time, timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from workspace.files.services import FilePermission

from ..models import MediaItem

# A shift of a century covers any clock left unset since the factory.
MAX_SHIFT = timedelta(days=36525)
# The time a date set without one lands on, as for a name giving the day
# only (filename_dates.py): it stays on that day in any timezone within
# twelve hours of the one it was set in.
_DAY_ONLY_TIME = time(12)
# The datetimes every database stores: a shift taking a row past them is
# skipped rather than failing the batch.
_EARLIEST = datetime(1, 1, 2, tzinfo=UTC)
_LATEST = datetime(9999, 12, 30, tzinfo=UTC)


def can_edit_capture_details(file_obj, permission):
    """Whether a viewer holding *permission* on the library photo *file_obj*
    may correct its date and place: they change what everyone sees, so it
    takes edit rights, as marking it sensitive does."""
    return (
        file_obj.deleted_at is None
        and permission is not None
        and permission >= FilePermission.EDIT
    )


def shift_taken_at(file_ids, delta):
    """Move the date of the photos *file_ids* by *delta*; returns how many
    moved. An undated photo has nothing to move and is left out."""
    rows = MediaItem.objects.filter(file_id__in=file_ids, taken_at__isnull=False)
    if delta > timedelta(0):
        rows = rows.filter(taken_at__lt=_LATEST - delta)
    else:
        rows = rows.filter(taken_at__gte=_EARLIEST - delta)
    return rows.update(
        taken_at=F("taken_at") + delta,
        taken_at_from_name=False,
        taken_at_by_user=True,
    )


def set_taken_at(file_ids, day, at, tz):
    """Date the photos *file_ids* on *day* at *at*, both in *tz*; returns
    how many were dated.

    Without *at*, each photo keeps its own time of day, so a batch moved to
    another day keeps its order; an undated one lands at noon.
    """
    rows = list(MediaItem.objects.filter(file_id__in=file_ids).only("taken_at"))
    for item in rows:
        item.taken_at = _combine(day, at or _time_of_day(item.taken_at, tz), tz)
        item.taken_at_from_name = False
        item.taken_at_by_user = True
    MediaItem.objects.bulk_update(
        rows, ["taken_at", "taken_at_from_name", "taken_at_by_user"]
    )
    return len(rows)


def set_location(file_ids, latitude, longitude):
    """Place the photos *file_ids* at *latitude*, *longitude*, or nowhere
    when both are None; returns how many were placed. The file's altitude
    belonged to the old position and goes with it."""
    return MediaItem.objects.filter(file_id__in=file_ids).update(
        latitude=latitude,
        longitude=longitude,
        altitude=None,
        location_by_user=True,
    )


def restore_from_file(file_ids, *, taken_at=False, location=False):
    """Hand the date, the place, or both of the photos *file_ids* back to
    the file; returns how many rows were corrected by hand before.

    The file is read again off-request, once the flags are down: its
    metadata (or its name) then dates and places it as on upload.
    """
    flags = {}
    corrected = Q()
    if taken_at:
        flags["taken_at_by_user"] = False
        corrected |= Q(taken_at_by_user=True)
    if location:
        flags["location_by_user"] = False
        corrected |= Q(location_by_user=True)
    if not flags:
        return 0
    to_read = [
        str(file_id)
        for file_id in MediaItem.objects.filter(
            corrected, file_id__in=file_ids
        ).values_list("file_id", flat=True)
    ]
    if not to_read:
        return 0
    # No analysis version: should the task fail, the hourly catch-up reads
    # the file instead (analysis.pending_media_qs).
    MediaItem.objects.filter(file_id__in=to_read).update(**flags, analysis_version=None)

    from ..tasks import reread_media

    transaction.on_commit(lambda: reread_media.delay(to_read))
    return len(to_read)


def _time_of_day(taken_at, tz):
    if taken_at is None:
        return _DAY_ONLY_TIME
    return timezone.localtime(taken_at, tz).time()


def _combine(day: date, at: time, tz):
    return datetime.combine(day, at.replace(tzinfo=None), tzinfo=tz)

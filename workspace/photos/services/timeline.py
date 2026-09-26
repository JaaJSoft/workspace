"""Page through the photo library in capture order.

The timeline reads newest first: dated photos by ``taken_at``, then the
Undated bucket by upload date. Pages are cut by keyset rather than offset, so
an upload landing while someone scrolls neither repeats nor skips a photo.

Photos are grouped by day, the days by month. A page runs on to the end of
the day it stops in, so a day is one group on screen however the pages fall;
the next page is appended after it and opens with the next day. Only a day
larger than ``DAY_OVERFLOW`` is cut, and the page continuing it opens with an
unlabelled group: the headers a page opens with are always decided against
the position it starts from, never repeated.

The Undated bucket has no days to group by. Its photos are laid out one by
one, so its pages simply go on filling the row the previous one stopped on.

An album sorted by hand is paged the same way on another key, its items'
``position`` (see ``manual_page``): no dates, no groups, one flat run of
tiles in the order the album holds them.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db.models import (
    BooleanField,
    Case,
    Count,
    Exists,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import ExtractYear
from django.utils import timezone

from workspace.files.actions import ActionRegistry
from workspace.files.models import FileFavorite
from workspace.files.services import FileService
from workspace.photos.models import AlbumItem

PAGE_SIZE = 60
DAY_OVERFLOW = 500

UNDATED = "undated"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_CURSOR_RE = re.compile(
    r"^(?P<bucket>[du])(?P<micros>-?\d{1,20})\.(?P<uuid>[0-9a-f-]{36})$"
)
_MANUAL_CURSOR_RE = re.compile(r"^m(?P<position>-?\d{1,20})\.(?P<uuid>[0-9a-f-]{36})$")
_DATE_RE = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?$")


@dataclass(frozen=True)
class Position:
    """Where a page starts: strictly after the last photo already shown.

    ``before`` bounds the sort key of the bucket (``taken_at`` for dated
    photos, the upload date for undated ones). ``after_uuid`` is set when the
    position continues a previous page and breaks ties at exactly ``before``;
    a position opened on a date has none, and starts a fresh section.
    """

    undated: bool = False
    before: datetime | None = None
    after_uuid: uuid.UUID | None = None

    @property
    def continues_page(self):
        return self.after_uuid is not None


START = Position()


def position_after(file_obj):
    """The position right after *file_obj* in timeline order."""
    taken_at = file_obj.media_item.taken_at
    if taken_at is None:
        return Position(
            undated=True, before=file_obj.created_at, after_uuid=file_obj.uuid
        )
    return Position(before=taken_at, after_uuid=file_obj.uuid)


def encode_cursor(file_obj):
    """The position right after *file_obj*, as a query-string token."""
    position = position_after(file_obj)
    micros = (position.before - _EPOCH) // timedelta(microseconds=1)
    return f"{'u' if position.undated else 'd'}{micros}.{position.after_uuid}"


def parse_cursor(raw):
    """Decode an ``encode_cursor`` token; ValueError when it is not one."""
    match = _CURSOR_RE.match(raw or "")
    if match is None:
        raise ValueError("malformed cursor")
    try:
        before = _EPOCH + timedelta(microseconds=int(match["micros"]))
    except OverflowError as exc:
        raise ValueError("cursor out of range") from exc
    return Position(
        undated=match["bucket"] == "u",
        before=before,
        after_uuid=uuid.UUID(match["uuid"]),
    )


def parse_date_position(raw, tz):
    """The position that opens the timeline on a year, a month or a day.

    ``2024``, ``2024-07`` and ``2024-07-14`` show that period's photos first
    (the most recent at the top) and everything older below; ``undated`` opens
    the Undated bucket. Days are taken in *tz*. ValueError on anything else.
    """
    if raw == UNDATED:
        return Position(undated=True)
    match = _DATE_RE.match(raw or "")
    if match is None:
        raise ValueError("malformed date")
    year = int(match["year"])
    month = int(match["month"] or 1)
    try:
        start = date(year, month, int(match["day"] or 1))
        if match["day"]:
            end = start + timedelta(days=1)
        elif match["month"]:
            end = date(year + month // 12, month % 12 + 1, 1)
        else:
            end = date(year + 1, 1, 1)
        before = timezone.make_aware(datetime.combine(end, datetime.min.time()), tz)
    except OverflowError as exc:
        raise ValueError("date out of range") from exc
    return Position(before=before)


def with_timeline_fields(files_qs, user):
    """Load what a tile shows: the MediaItem and MediaInfo rows, the favorite
    star, and whether the file's folder is one the user can browse in Files
    (their own or one of their groups') or only the file itself was shared
    with them."""
    return files_qs.select_related("media_item", "media_info").annotate(
        is_favorite=Exists(
            FileFavorite.objects.filter(owner=user, file_id=OuterRef("pk"))
        ),
        in_browsable_folder=Case(
            When(
                Q(owner=user) | Q(group__in=user.groups.values("pk")),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def _after(field, position):
    if position.before is None:
        return Q()
    if position.after_uuid is None:
        return Q(**{f"{field}__lt": position.before})
    return Q(**{f"{field}__lt": position.before}) | Q(
        **{field: position.before, "uuid__lt": position.after_uuid}
    )


def _dated(files_qs, position):
    return (
        files_qs.filter(media_item__taken_at__isnull=False)
        .filter(_after("media_item__taken_at", position))
        .order_by("-media_item__taken_at", "-uuid")
    )


def _undated(files_qs, position):
    undated = files_qs.filter(media_item__taken_at__isnull=True)
    if position.undated:
        undated = undated.filter(_after("created_at", position))
    return undated.order_by("-created_at", "-uuid")


def _rest_of_day(files_qs, last, tz):
    """The photos after *last* taken on the same local day, up to the cap."""
    day = timezone.localtime(last.media_item.taken_at, tz).date()
    day_start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return list(
        _dated(files_qs, position_after(last)).filter(
            media_item__taken_at__gte=day_start
        )[:DAY_OVERFLOW]
    )


def _has_more(files_qs, position):
    if position.undated:
        return _undated(files_qs, position).exists()
    return (
        _dated(files_qs, position).exists()
        or files_qs.filter(media_item__taken_at__isnull=True).exists()
    )


@dataclass
class TimelinePage:
    entries: list
    next_cursor: str | None
    photos: list


def timeline_page(files_qs, position, tz, *, page_size=None):
    """The page of *files_qs* starting at *position*, grouped in *tz*.

    *files_qs* is a library queryset (see ``queries.library_files``) already
    narrowed by the active filters and prepared by ``with_timeline_fields``.
    """
    page_size = page_size or PAGE_SIZE
    photos = []
    if not position.undated:
        photos = list(_dated(files_qs, position)[:page_size])
        if len(photos) == page_size:
            photos += _rest_of_day(files_qs, photos[-1], tz)
    if len(photos) < page_size:
        photos += list(_undated(files_qs, position)[: page_size - len(photos)])

    next_cursor = None
    if photos and _has_more(files_qs, position_after(photos[-1])):
        next_cursor = encode_cursor(photos[-1])
    return TimelinePage(
        entries=_entries(photos, position, tz),
        next_cursor=next_cursor,
        photos=photos,
    )


def mark_favorite_toggles(photos, user):
    """Set ``can_favorite`` on each photo, as the files action registry decides.

    The tile's star is a file action like any other; asking the registry keeps
    it from offering what the favorite endpoint would refuse. Permissions are
    read in one pass for the whole page.
    """
    permissions = FileService.get_permissions_bulk(user, photos)
    for photo in photos:
        photo.can_favorite = ActionRegistry.is_action_available(
            "toggle_favorite", user, photo, permission=permissions[photo.pk]
        )


def _entries(photos, position, tz):
    """Month headers, day groups and undated photos, in display order."""
    previous_day = None
    if position.continues_page and not position.undated:
        previous_day = timezone.localtime(position.before, tz).date()
    undated_open = position.undated and position.continues_page

    entries = []
    group = None
    for photo in photos:
        taken_at = photo.media_item.taken_at
        if taken_at is None:
            if not undated_open:
                entries.append({"kind": "undated"})
                undated_open = True
            entries.append({"kind": "photo", "file": photo})
            continue
        day = timezone.localtime(taken_at, tz).date()
        if group is None or day != group["date"]:
            if previous_day is None or (day.year, day.month) != (
                previous_day.year,
                previous_day.month,
            ):
                entries.append({"kind": "month", "date": day})
            group = {
                "kind": "day",
                "date": day,
                "continued": day == previous_day,
                "photos": [],
            }
            entries.append(group)
            previous_day = day
        group["photos"].append(photo)
    return entries


def year_counts(files_qs, tz):
    """``[(year, count), ...]`` newest first, then ``(None, count)`` if undated."""
    rows = (
        files_qs.filter(media_item__taken_at__isnull=False)
        .annotate(year=ExtractYear("media_item__taken_at", tzinfo=tz))
        .order_by()
        .values("year")
        .annotate(count=Count("pk"))
        .order_by("-year")
    )
    counts = [(row["year"], row["count"]) for row in rows]
    undated = files_qs.filter(media_item__taken_at__isnull=True).count()
    if undated:
        counts.append((None, undated))
    return counts


def with_album_positions(files_qs, album):
    """Annotate each file of *album* with ``album_position``, its manual rank."""
    return files_qs.annotate(
        album_position=Subquery(
            AlbumItem.objects.filter(album=album, file_id=OuterRef("pk")).values(
                "position"
            )[:1]
        )
    )


def parse_manual_cursor(raw):
    """Decode a ``manual_page`` cursor as ``(position, uuid)``; ValueError
    when it is not one."""
    match = _MANUAL_CURSOR_RE.match(raw or "")
    if match is None:
        raise ValueError("malformed cursor")
    return int(match["position"]), uuid.UUID(match["uuid"])


def manual_page(files_qs, after=None, *, page_size=None):
    """The page of *files_qs* in album order, starting after *after*.

    *files_qs* carries ``album_position`` (``with_album_positions``) and the
    tile fields. *after* is a decoded cursor, None for the first page. Ties
    on a position, left by two concurrent adds, break on the file's uuid.
    """
    page_size = page_size or PAGE_SIZE
    ordered = files_qs.order_by("album_position", "uuid")
    if after is not None:
        position, after_uuid = after
        ordered = ordered.filter(
            Q(album_position__gt=position)
            | Q(album_position=position, uuid__gt=after_uuid)
        )
    rows = list(ordered[: page_size + 1])
    photos = rows[:page_size]
    next_cursor = None
    if len(rows) > page_size:
        last = photos[-1]
        next_cursor = f"m{last.album_position}.{last.uuid}"
    return TimelinePage(
        entries=[{"kind": "photo", "file": photo} for photo in photos],
        next_cursor=next_cursor,
        photos=photos,
    )

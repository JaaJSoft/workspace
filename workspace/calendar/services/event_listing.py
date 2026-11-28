"""The events a person sees, recurring series expanded into occurrences.

One list, read two ways. Between two instants it holds what overlaps the
window: a month of the grid. From one instant on, with no end, it holds what
starts from there: the agenda, which has no last page as long as a series
repeats. Both come out in agenda order, an all-day event at the start of its
day in the reader's zone.
"""

from datetime import UTC, datetime, time
from typing import NamedTuple
from uuid import UUID

from django.db.models import OuterRef, Prefetch, Q, Subquery
from django.utils import timezone
from django.utils.functional import cached_property

from workspace.common.serializers import InstantField

from ..models import Event, EventMember, Poll
from ..queries import undeclined_events, visible_events_q
from ..recurrence import next_occurrences_after, occurrences_in_range, overlap_q
from ..serializers import EventSerializer
from .timezones import all_day_label

_instant = InstantField().to_representation

# How many events the list with no end is read from one `start`. Reading a
# page costs what reading every row before it costs - each series is walked
# from the start again - so the depth is bounded, and a reader going further
# moves `start` forward to the last event it holds.
AGENDA_DEPTH = 10_000


class Endless(Exception):
    """The list has no end, so it has no number of rows: it was asked for
    its count."""


class TooDeep(IndexError):
    """Rows of the list with no end were asked for past ``AGENDA_DEPTH``."""


class Listed(NamedTuple):
    """One row of the list: where it sorts, the database row behind it (what
    a notification is keyed on), and what the API answers."""

    position: tuple
    row_id: UUID
    data: dict


def with_details(events):
    """*events* with what serializing them reads, in a fixed number of queries."""
    polls = Poll.objects.filter(event=OuterRef("pk"))
    return (
        events.select_related("owner", "calendar", "recurrence_parent")
        .prefetch_related(
            Prefetch(
                "members",
                queryset=EventMember.objects.select_related("user").order_by(
                    "created_at", "uuid"
                ),
            )
        )
        .annotate(
            _poll_uuid=Subquery(polls.values("uuid")[:1]),
            _poll_title=Subquery(polls.values("title")[:1]),
        )
    )


def _when(value, all_day):
    """How a start or an end reads: a day for an all-day event, an instant
    otherwise."""
    if value is None:
        return None
    if all_day:
        return value.astimezone(UTC).date().isoformat()
    return _instant(value)


def occurrence_data(series_data, series, occurrence_start):
    """What an occurrence the rule gives reads as: its series, at its slot."""
    duration = (series.end - series.start) if series.end else None
    occurrence_end = occurrence_start + duration if duration else None
    return {
        **series_data,
        "start": _when(occurrence_start, series.all_day),
        "end": _when(occurrence_end, series.all_day),
        "original_start": _instant(occurrence_start),
    }


class EventListing:
    """The list, as the paginator of the API reads a queryset: sliced, and
    counted when asked.

    *start* alone lists what starts from it on; with *end*, what overlaps
    the window. *narrow* takes a queryset of events and keeps the ones asked
    for (the filters of the request); left out, the events the reader
    declined are dropped. *context* is the serializer's: it names the
    request, or the ``viewer`` a task lists for.
    """

    def __init__(self, user, *, start, end=None, narrow=None, tz=None, context=None):
        self.user = user
        self.start = start
        self.end = end
        self.narrow = narrow or (lambda events: undeclined_events(events, user))
        self.tz = tz or timezone.get_current_timezone()
        self.context = context if context is not None else {"viewer": user}
        self._window = None

    def count(self):
        if self.end is None:
            raise Endless
        return len(self._in_window())

    def __getitem__(self, piece):
        if self.end is not None:
            return self._in_window()[piece]
        # The paginator reads one row past the page to know whether one
        # follows.
        if piece.stop > AGENDA_DEPTH + 1:
            raise TooDeep
        return self._from_start(piece.stop)[piece]

    # -- what both readings share --

    @cached_property
    def _visible(self):
        # Read once: every query of one reading starts from it.
        return visible_events_q(self.user)

    def _events(self):
        return self.narrow(Event.objects.filter(self._visible, is_cancelled=False))

    def _position(self, start, all_day, row_id):
        """Where a row sorts. An all-day event sits at the start of its day
        in the reader's zone, ahead of that day's timed events: at its
        storage instant, a reader west of UTC would find tomorrow's all-day
        event among tonight's."""
        if all_day:
            day = start.astimezone(UTC).date()
            start = datetime.combine(day, time.min, tzinfo=self.tz)
        return (start, str(row_id))

    def _rows(self, events):
        events = list(events)
        answers = EventSerializer(events, many=True, context=self.context).data
        return [
            Listed(
                self._position(event.start, event.all_day, event.uuid), event.uuid, data
            )
            for event, data in zip(events, answers, strict=True)
        ]

    def _series_data(self, series):
        # Once per series: its occurrences differ by their times alone.
        return EventSerializer(series, context=self.context).data

    def _occurrence(self, series, series_data, occurrence_start):
        return Listed(
            self._position(occurrence_start, series.all_day, series.uuid),
            series.uuid,
            occurrence_data(series_data, series, occurrence_start),
        )

    def _series(self):
        return self._events().filter(is_recurring=True, recurrence_parent__isnull=True)

    # -- between two instants --

    def _in_window(self):
        if self._window is not None:
            return self._window
        in_window = overlap_q(self.start, self.end)
        listed = self._rows(
            with_details(
                self._events().filter(
                    in_window, is_recurring=False, recurrence_parent__isnull=True
                )
            )
        )

        # Series with an occurrence in the window, or with one moved into it
        # from a slot outside it (past the end of the rule included).
        moved_in = Event.objects.filter(
            in_window, recurrence_parent__isnull=False, is_cancelled=False
        ).values("recurrence_parent_id")
        series = {
            row.uuid: row
            for row in with_details(
                self._series().filter(
                    Q(start__lt=self.end)
                    & (
                        Q(recurrence_until__isnull=True)
                        | Q(recurrence_until__gte=self.start)
                    )
                    | Q(uuid__in=moved_in)
                )
            )
        }
        if series:
            # An occurrence changed on its own replaces its slot wherever it
            # now sits, and is drawn at its own time: one moved into the
            # window is in, one moved out is not, a cancelled one never is.
            replaced = set(
                Event.objects.filter(
                    recurrence_parent_id__in=list(series),
                    original_start__isnull=False,
                ).values_list("recurrence_parent_id", "original_start")
            )
            for row in series.values():
                data = self._series_data(row)
                listed.extend(
                    self._occurrence(row, data, occurrence_start)
                    for occurrence_start in occurrences_in_range(
                        row, self.start, self.end
                    )
                    if (row.uuid, occurrence_start) not in replaced
                )
            # Through what the reader can see: an occurrence changed on its
            # own carries its own invitees, and someone taken off it finds
            # its slot empty.
            changed = self._events().filter(
                in_window, recurrence_parent_id__in=list(series)
            )
            listed.extend(self._rows(with_details(changed)))

        listed.sort(key=lambda row: row.position)
        self._window = listed
        return listed

    # -- from an instant on --

    def _from_start(self, wanted):
        """The first *wanted* rows starting from ``start`` on.

        Timed events count from the instant, all-day events from the local
        day it falls on, so today's stay listed all day long.
        """
        first_day = all_day_label(self.start.astimezone(self.tz).date())

        # Occurrences changed on their own are rows of this stream too.
        # Timed and all-day rows are read as two streams, each in agenda
        # order: a single ORDER BY start would rank them by storage instant,
        # and its LIMIT could cut an all-day row the merge places before a
        # timed one it kept.
        events = with_details(self._events().filter(is_recurring=False))
        listed = self._rows(
            [
                *events.filter(start__gte=self.start, all_day=False).order_by(
                    "start", "uuid"
                )[:wanted],
                *events.filter(start__gte=first_day, all_day=True).order_by(
                    "start", "uuid"
                )[:wanted],
            ]
        )

        series = list(
            with_details(
                self._series().filter(
                    Q(recurrence_until__isnull=True)
                    | Q(recurrence_until__gte=min(self.start, first_day))
                )
            )
        )
        replaced = set()
        if series:
            replaced = set(
                Event.objects.filter(
                    recurrence_parent_id__in=[row.uuid for row in series],
                    original_start__isnull=False,
                ).values_list("recurrence_parent_id", "original_start")
            )
        for row in series:
            data = self._series_data(row)
            kept = 0
            # The stream is walked until enough slots are kept: asking it
            # for *wanted* of them would stop short of every replaced one.
            for occurrence_start in next_occurrences_after(
                row, first_day if row.all_day else self.start
            ):
                if kept >= wanted:
                    break
                if (row.uuid, occurrence_start) in replaced:
                    continue
                listed.append(self._occurrence(row, data, occurrence_start))
                kept += 1

        listed.sort(key=lambda row: row.position)
        return listed[:wanted]


class Occurrence(NamedTuple):
    """What a uuid and an optional slot name: an event, or one occurrence of
    a series. ``changed`` is the row of an occurrence changed on its own."""

    series: Event
    changed: Event | None
    original_start: datetime | None

    @property
    def row(self):
        return self.changed or self.series


def find_occurrence(user, uuid, original_start=None):
    """The event *uuid* names for *user*, or None when they cannot see it.

    With *original_start*, the occurrence of the series at that slot. A slot
    the series dropped is no occurrence anymore, and neither is, for someone
    taken off it, one changed on its own: it carries its own invitees. Such
    an occurrence is found through its series, and by the uuid of its own row
    as well: the links of earlier notifications carry it.
    """
    visible = with_details(Event.objects.filter(visible_events_q(user)))
    row = visible.filter(pk=uuid).first()
    if row is None:
        if original_start is None:
            return None
        # Someone invited to one occurrence only does not see its series.
        changed = visible.filter(
            recurrence_parent_id=uuid, original_start=original_start
        ).first()
        if changed is None or changed.is_cancelled:
            return None
        return Occurrence(changed.recurrence_parent, changed, original_start)
    if row.recurrence_parent_id:
        if row.is_cancelled:
            return None
        return Occurrence(row.recurrence_parent, row, row.original_start)
    if original_start is None or not row.is_recurring:
        return Occurrence(row, None, None)
    replaced = Event.objects.filter(
        recurrence_parent=row, original_start=original_start
    )
    if not replaced.exists():
        return Occurrence(row, None, original_start)
    changed = visible.filter(
        recurrence_parent=row, original_start=original_start, is_cancelled=False
    ).first()
    if changed is None:
        return None
    return Occurrence(row, changed, original_start)


def occurrence_answer(occurrence, context):
    """What the API answers for *occurrence*."""
    if occurrence.changed is None and occurrence.original_start is not None:
        series = occurrence.series
        return occurrence_data(
            EventSerializer(series, context=context).data,
            series,
            occurrence.original_start,
        )
    return EventSerializer(occurrence.row, context=context).data

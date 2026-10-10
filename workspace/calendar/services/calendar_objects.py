"""Calendar objects in the database: find, list, store and delete them.

A calendar object is a series master (or single event) plus its exception
rows. ``ical_objects`` turns one into text and back; this module maps the
parsed form onto rows.
"""

from django.db import transaction
from django.db.models import Q

from workspace.common.uuids import parse_uuid_or_none

from ..models import Event
from .ical_objects import InvalidObject, object_uid
from .recurrence_rule import apply_rule

_EVENT_COLUMNS = [
    "title",
    "description",
    "location",
    "start",
    "end",
    "all_day",
    "timezone",
    "ical_sequence",
    "is_cancelled",
    "ical_extra",
    "recurrence_rule",
    "is_recurring",
    "recurrence_until",
    "updated_at",
]


def calendar_masters(calendar):
    """The series masters and single events of *calendar*, exceptions prefetched."""
    return (
        Event.objects.filter(calendar=calendar, recurrence_parent__isnull=True)
        .prefetch_related("exceptions")
        .order_by("start", "uuid")
    )


def masters_in_range(masters, range_start=None, range_end=None):
    """Narrow *masters* to the objects with an occurrence in the window.

    A loose test, as RFC 4791 9.9 allows: a series is kept while its derived
    bound has not passed, even if no single occurrence lands in the window.
    """
    if range_end is not None:
        masters = masters.filter(start__lt=range_end)
    if range_start is not None:
        masters = masters.filter(
            Q(is_recurring=True, recurrence_until__isnull=True)
            | Q(is_recurring=True, recurrence_until__gte=range_start)
            | Q(is_recurring=False, end__gt=range_start)
            | Q(is_recurring=False, end__isnull=True, start__gte=range_start)
        )
    return masters


def find_object(calendar, name):
    """The master served as *name* in *calendar*, or None."""
    masters = Event.objects.filter(calendar=calendar, recurrence_parent__isnull=True)
    master = masters.filter(dav_name=name).first()
    if master is None and name.endswith(".ics"):
        uuid = parse_uuid_or_none(name.removesuffix(".ics"))
        if uuid is not None:
            master = masters.filter(uuid=uuid, dav_name="").first()
    return master


def find_objects(calendar, names):
    """``{name: master}`` for the *names* that exist in *calendar*."""
    found = {}
    for name in names:
        master = find_object(calendar, name)
        if master is not None:
            found[name] = master
    return found


@transaction.atomic
def store_object(calendar, name, parsed, existing=None):
    """Write *parsed* as the object *name* of *calendar*; return its master.

    *existing* is the master currently served under *name*, None for a create.
    Exceptions are replaced as a set: an occurrence override the client no
    longer sends is an edit it undid.
    """
    if existing is not None and object_uid(existing) != parsed.uid:
        raise InvalidObject("An object's UID cannot change.", "no-uid-conflict")
    clash = Event.objects.filter(calendar=calendar, ical_uid=parsed.uid)
    if existing is not None:
        clash = clash.exclude(pk=existing.pk)
    if clash.exists():
        raise InvalidObject("Another object already has this UID.", "no-uid-conflict")

    if existing is None:
        master = Event(
            calendar=calendar,
            owner=calendar.owner,
            dav_name=name,
            ical_uid=parsed.uid,
            source=Event.Source.CALDAV,
        )
    else:
        master = existing
    _apply(master, parsed.master, parsed.master.rule)
    if existing is None:
        master.save()
    else:
        master.save(update_fields=_EVENT_COLUMNS)

    current = {exc.original_start: exc for exc in master.exceptions.all()}
    kept = set()
    for override in parsed.overrides:
        exc = current.get(override.recurrence_id)
        if exc is None:
            exc = Event(
                calendar=master.calendar,
                owner=master.owner,
                recurrence_parent=master,
                original_start=override.recurrence_id,
                source=Event.Source.CALDAV,
            )
            _apply(exc, override, "")
            exc.save()
        else:
            _apply(exc, override, "")
            exc.save(update_fields=_EVENT_COLUMNS)
        kept.add(override.recurrence_id)
    for start, exc in current.items():
        if start not in kept:
            exc.delete()
    return master


def _apply(event, parsed, rule):
    event.title = parsed.title
    event.description = parsed.description
    event.location = parsed.location
    event.start = parsed.start
    event.end = parsed.end
    event.all_day = parsed.all_day
    event.timezone = parsed.timezone
    event.ical_sequence = parsed.sequence
    event.is_cancelled = parsed.cancelled
    event.ical_extra = parsed.extra
    apply_rule(event, rule)

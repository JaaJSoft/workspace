"""Calendar objects as iCalendar text, both directions, with no database involved.

A calendar object is what CalDAV serves as one ``.ics`` resource: a single
event, or a series master with its exception rows, as one VCALENDAR whose
VEVENTs share a UID (RFC 4791 4.1). The ICS feed renders every object of a
calendar into a single VCALENDAR the same way.

Every reader of iCalendar text goes through here: ``parse_object`` for a
CalDAV PUT, which must hold exactly one object, and ``read_calendar`` for a
subscribed feed or an emailed invitation, which hold any number. Both produce
the same objects; what to write from them is each caller's policy.

Fidelity is the point. A client compares what it PUT with what it GETs, and
anything the server drops is gone from the phone on the next sync. So two
kinds of lines are stored as the client sent them and re-emitted verbatim:
the recurrence lines (``Event.recurrence_rule``) and every VEVENT line this
model has no column for (``Event.ical_extra``: alarms, attendees, categories,
X- properties...). Only the properties backed by a column are re-rendered.

Both are read off the raw, unfolded content lines rather than re-serialized
from icalendar's parse: re-serializing reorders RRULE parts and requotes
parameters, which is a different text even when it means the same thing.
icalendar still does the semantic reading (dates, zones, text unescaping).
"""

import functools
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
import x_wr_timezone
from icalendar.timezone.tzid import tzid_from_tzinfo

from workspace.common.logging import scrub

from .ics_builder import add_event_times
from .timezones import event_timezone, normalize_all_day, zone_from_tzid

logger = logging.getLogger(__name__)

PRODID = "-//Workspace//Calendar//EN"

# VEVENT properties backed by a column, or that the server writes itself.
_CONSUMED = frozenset(
    {
        "UID",
        "DTSTAMP",
        "CREATED",
        "LAST-MODIFIED",
        "DTSTART",
        "DTEND",
        "DURATION",
        "SUMMARY",
        "DESCRIPTION",
        "LOCATION",
        "SEQUENCE",
        "RECURRENCE-ID",
    }
)
_RECURRENCE = frozenset({"RRULE", "RDATE", "EXDATE", "EXRULE"})
# Components that only ever sit at the top of a body, never inside another.
_TOP_LEVEL = frozenset({"VCALENDAR", "VEVENT", "VTODO", "VJOURNAL", "VTIMEZONE"})

_NAME_RE = re.compile(r"[A-Za-z0-9-]+")
_TZID_RE = re.compile(r';TZID=(?:"([^"]*)"|([^;:]*))', re.IGNORECASE)
_UNFOLD_RE = re.compile(r"\r?\n[ \t]")

# The width of the title and location columns.
_MAX_TEXT = 255
# The width of Event.ical_uid.
_MAX_UID = 512
# The width of Event.external_organizer.
_MAX_EMAIL = 254

# RFC 5545 3.1: content lines SHOULD NOT exceed 75 octets.
_FOLD_OCTETS = 75


class InvalidObject(ValueError):
    """A body that is not one storable calendar object.

    *condition* names the CalDAV precondition it fails (RFC 4791 5.3.2.1), so
    the DAV layer can tell the client which rule it broke.
    """

    def __init__(self, message, condition="valid-calendar-data"):
        super().__init__(message)
        self.condition = condition


# ── Rendering ─────────────────────────────────────────────────────────


def render_object(master, exceptions=()):
    """One calendar object - *master* and its exception rows - as a VCALENDAR."""
    tzids = set()
    blocks = _object_blocks(master, exceptions, tzids)
    return _vcalendar(blocks, tzids)


def render_calendar(name, objects):
    """Every object of a calendar in one VCALENDAR, as an ICS feed serves it.

    *objects* yields ``(master, exceptions)`` pairs.
    """
    tzids = set()
    blocks = []
    for master, exceptions in objects:
        blocks.extend(_object_blocks(master, exceptions, tzids))
    header = [f"X-WR-CALNAME:{icalendar.vText(name).to_ical().decode()}"]
    return _vcalendar(blocks, tzids, header)


def object_uid(master):
    """The UID a master is served under: the client's, or its own uuid."""
    return master.ical_uid or str(master.uuid)


def _object_blocks(master, exceptions, tzids):
    cancelled = [exc.original_start for exc in exceptions if exc.is_cancelled]
    blocks = [_vevent(master, master, tzids, cancelled)]
    blocks.extend(
        _vevent(exc, master, tzids)
        for exc in sorted(exceptions, key=lambda exc: exc.original_start)
        if not exc.is_cancelled
    )
    return blocks


def _vevent(event, master, tzids, cancelled=()):
    vevent = icalendar.Event()
    vevent.add("UID", object_uid(master))
    stamp = (event.updated_at or datetime.now(UTC)).astimezone(UTC)
    vevent.add("DTSTAMP", stamp)
    if event.created_at:
        vevent.add("CREATED", event.created_at.astimezone(UTC))
    vevent.add("LAST-MODIFIED", stamp)
    if event is not master:
        vevent.add("RECURRENCE-ID", _occurrence_value(master, event.original_start))
    add_event_times(vevent, event)
    vevent.add("SUMMARY", event.title)
    if event.description:
        vevent.add("DESCRIPTION", event.description)
    if event.location:
        vevent.add("LOCATION", event.location)
    if event.ical_sequence:
        vevent.add("SEQUENCE", event.ical_sequence)
    if event.is_cancelled:
        vevent.add("STATUS", "CANCELLED")

    extra = _lines(event.ical_extra)
    if event.external_organizer and not any(
        _property_name(line) == "ORGANIZER" for line in extra
    ):
        vevent.add(
            "ORGANIZER", icalendar.vCalAddress(f"mailto:{event.external_organizer}")
        )
    # An occurrence cancelled in the app is an exception row with nothing
    # left to say: clients know it as an EXDATE.
    for start in sorted(cancelled):
        vevent.add("EXDATE", _occurrence_value(master, start))

    if not event.all_day and event_timezone(event) is not None:
        tzids.add(event.timezone)
    if (
        event is not master
        and event_timezone(master) is not None
        and not master.all_day
    ):
        tzids.add(master.timezone)

    verbatim = _recurrence_lines(event.recurrence_rule) if event is master else []
    verbatim += extra
    for line in verbatim:
        tzids.update(_tzids(line))

    text = vevent.to_ical().decode()
    end = "END:VEVENT\r\n"
    return text[: -len(end)] + "".join(_fold(line) for line in verbatim) + end


def _occurrence_value(master, instant):
    """*instant* typed like the master's DTSTART, as RECURRENCE-ID/EXDATE need."""
    if master.all_day:
        return instant.astimezone(UTC).date()
    zone = event_timezone(master)
    return instant.astimezone(zone) if zone else instant.astimezone(UTC)


def _vcalendar(blocks, tzids, header=()):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        *header,
    ]
    out = ["".join(_fold(line) for line in lines)]
    out.extend(_vtimezone(tzid) for tzid in sorted(tzids))
    out.extend(blocks)
    out.append("END:VCALENDAR\r\n")
    return "".join(out)


@functools.lru_cache(maxsize=128)
def _vtimezone(tzid):
    """The VTIMEZONE a client needs to read *tzid*; empty for an unknown zone."""
    try:
        ZoneInfo(tzid)
        return icalendar.Timezone.from_tzid(tzid).to_ical().decode()
    except ZoneInfoNotFoundError, ValueError:
        return ""


def _fold(line):
    """Fold one content line at 75 octets, never inside a UTF-8 sequence."""
    encoded = line.encode()
    if len(encoded) <= _FOLD_OCTETS:
        return line + "\r\n"
    parts = []
    limit = _FOLD_OCTETS
    while encoded:
        cut = min(limit, len(encoded))
        # Back off continuation bytes (10xxxxxx) so a character stays whole.
        while cut < len(encoded) and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode())
        encoded = encoded[cut:]
        limit = _FOLD_OCTETS - 1  # the leading space of a continuation
    return "\r\n ".join(parts) + "\r\n"


def _recurrence_lines(rule_text):
    """The stored recurrence lines, each a complete content line.

    The REST API accepts a bare RRULE body (``FREQ=WEEKLY;BYDAY=MO``), which
    the recurrence engine reads fine but is no iCalendar content line.
    """
    return [
        line if _property_name(line) in _RECURRENCE else f"RRULE:{line}"
        for line in _lines(rule_text)
    ]


def _lines(text):
    return [line for line in (text or "").splitlines() if line.strip()]


def _property_name(line):
    match = _NAME_RE.match(line)
    return match.group(0).upper() if match else ""


def _params(line):
    """The ``NAME;PARAM=...`` part of a content line, before its value."""
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ":" and not in_quotes:
            return line[:index]
    return line


def _tzids(line):
    return {quoted or bare for quoted, bare in _TZID_RE.findall(_params(line))}


# ── Parsing ───────────────────────────────────────────────────────────


@dataclass
class ParsedEvent:
    """One VEVENT, mapped onto Event columns."""

    title: str
    description: str
    location: str
    # None only out of a lenient read: an iTIP CANCEL may name its event by UID
    # (and RECURRENCE-ID) alone.
    start: datetime | None
    end: datetime | None
    all_day: bool
    timezone: str
    sequence: int
    cancelled: bool
    recurrence_id: datetime | None = None
    rule: str = ""
    extra: str = ""
    # The email of ORGANIZER, which stays in *extra* as the client wrote it.
    organizer: str = ""
    # The raw RECURRENCE-ID line, for an occurrence stored as a plain event.
    recurrence_line: str = ""


@dataclass
class ParsedObject:
    """The VEVENTs sharing a UID: a series master (or single event) and its
    exceptions. *master* is None when the body only holds occurrences of a
    series it does not carry, as an invitation to a single date does."""

    uid: str
    master: ParsedEvent | None
    overrides: list[ParsedEvent] = field(default_factory=list)


@dataclass
class ParsedCalendar:
    """Every object of a VCALENDAR, in the order they first appear.

    *unreadable* holds the UIDs of the events that could not be read: the
    text still lists them, so a reader that keeps what it already has must
    not take them for events that are gone.
    """

    method: str
    objects: list[ParsedObject]
    unreadable: set[str] = field(default_factory=set)


def parse_object(data, default_tz=UTC):
    """Read a PUT body into one calendar object, or raise InvalidObject.

    *default_tz* resolves floating times: "local time of the observer", and
    the calendar's owner is the closest observer there is.
    """
    _method, parsed, _unreadable = _read(data, default_tz, strict=True)
    if not parsed:
        raise InvalidObject(
            "The object holds no event.", "supported-calendar-component"
        )
    uids = {uid for uid, _event in parsed}
    if any(len(uid) > _MAX_UID for uid in uids):
        raise InvalidObject("The UID is too long.", "valid-calendar-object-resource")
    if len(uids) != 1 or "" in uids:
        raise InvalidObject(
            "Every event of an object must carry the same UID.",
            "valid-calendar-object-resource",
        )
    masters = [event for _uid, event in parsed if event.recurrence_id is None]
    if len(masters) > 1:
        raise InvalidObject(
            "An object holds at most one series.", "valid-calendar-object-resource"
        )
    (obj,) = _group(parsed)
    if obj.master is None and len(obj.overrides) != 1:
        raise InvalidObject(
            "Occurrences without their series are not supported.",
            "valid-calendar-object-resource",
        )
    return standalone(obj)


def read_calendar(data, default_tz=UTC):
    """Read a whole VCALENDAR - a feed, an invitation - into its objects.

    Lenient where ``parse_object`` is strict, because one broken event must
    not cost the rest of a feed: components other than VEVENT are skipped, and
    so is a VEVENT without a UID or with a value icalendar cannot read. A
    VEVENT without DTSTART is kept with ``start=None``; each caller decides
    whether it needs one. Raises InvalidObject only when the text is not an
    iCalendar object at all.
    """
    method, parsed, unreadable = _read(data, default_tz, strict=False)
    return ParsedCalendar(method=method, objects=_group(parsed), unreadable=unreadable)


def standalone(obj):
    """*obj*, with an occurrence of a series it does not hold made a plain event.

    One occurrence of somebody else's series (an invitation to a single date)
    is stored as a single event, its RECURRENCE-ID kept as an extra line so it
    goes back to the client as it came. Of several such occurrences, the
    earliest is kept: one UID is one row.
    """
    if obj.master is not None or not obj.overrides:
        return obj
    first = min(obj.overrides, key=lambda event: event.recurrence_id)
    master = replace(
        first,
        recurrence_id=None,
        recurrence_line="",
        extra="\n".join([first.recurrence_line, *_lines(first.extra)]),
    )
    return ParsedObject(uid=obj.uid, master=master)


def without_components(obj, name):
    """*obj* with every *name* component (``VALARM``) dropped from its events."""

    def strip(event):
        kept, depth = [], 0
        for line in _lines(event.extra):
            prop = _property_name(line)
            value = line.split(":", 1)[1].strip().upper() if ":" in line else ""
            if not depth and prop == "BEGIN" and value == name:
                depth = 1
            elif depth:
                depth += {"BEGIN": 1, "END": -1}.get(prop, 0)
            else:
                kept.append(line)
        return replace(event, extra="\n".join(kept))

    return replace(
        obj,
        master=strip(obj.master) if obj.master is not None else None,
        overrides=[strip(override) for override in obj.overrides],
    )


def standalone_recurrence_id(extra, default_tz=UTC):
    """The occurrence a plain event stands for, as ``standalone`` kept it.

    The instant of the RECURRENCE-ID line in *extra*, or None for an event that
    is not one occurrence of somebody else's series.
    """
    line = next(
        (line for line in _lines(extra) if _property_name(line) == "RECURRENCE-ID"),
        None,
    )
    if line is None:
        return None
    try:
        vevent = icalendar.Event.from_ical(f"BEGIN:VEVENT\r\n{line}\r\nEND:VEVENT\r\n")
        instant, _ = _instant(vevent.RECURRENCE_ID, default_tz)
    except ValueError, TypeError, KeyError, IndexError:
        return None
    return instant


def is_dated(obj):
    """Whether every event of *obj* has a start."""
    return all(
        event.start is not None for event in [obj.master, *obj.overrides] if event
    )


def dated(obj):
    """*obj* without its undated occurrences, or None when its master is undated."""
    if obj.master is None or obj.master.start is None:
        return None
    return replace(obj, overrides=[o for o in obj.overrides if o.start is not None])


def _read(data, default_tz, strict):
    """``(METHOD, [(uid, ParsedEvent)], unreadable UIDs)`` for *data*'s VEVENTs."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            if strict:
                raise InvalidObject("The body is not UTF-8.") from exc
            data = data.decode("utf-8", errors="replace")

    raw_blocks = _raw_vevents(_UNFOLD_RE.sub("", data).splitlines(), strict)
    try:
        calendar = icalendar.Calendar.from_ical(data)
    except ValueError, IndexError, KeyError, TypeError:
        raise InvalidObject("The body is not an iCalendar object.") from None
    calendar = _with_declared_zone(calendar)
    vevents = [c for c in calendar.subcomponents if c.name == "VEVENT"]
    if len(vevents) != len(raw_blocks):
        raise InvalidObject("The events could not be read.")

    parsed, unreadable = [], set()
    for vevent, raw in zip(vevents, raw_blocks, strict=True):
        try:
            uid, event = _parse_vevent(vevent, raw, default_tz, strict)
        except InvalidObject:
            if strict:
                raise
            uid = str(vevent.get("UID", "")).strip()
            logger.info("Skipped an unreadable event: %s", scrub(uid))
            if uid:
                unreadable.add(uid)
            continue
        if not strict and (not uid or len(uid) > _MAX_UID):
            logger.info("Skipped an event without a usable UID")
            continue
        parsed.append((uid, event))
    return str(calendar.get("METHOD", "")).strip().upper(), parsed, unreadable


def _with_declared_zone(calendar):
    """*calendar* with its X-WR-TIMEZONE applied to UTC and floating times.

    Google and others publish UTC times plus that calendar-wide zone, which no
    RFC defines: read as plain UTC, a weekly 09:00 drifts by an hour across
    every daylight-saving switch.
    """
    try:
        return x_wr_timezone.to_standard(calendar)
    except ZoneInfoNotFoundError, ValueError:
        return calendar


def _group(parsed):
    """Gather ``(uid, event)`` pairs into objects, in order of first appearance.

    A repeated master or occurrence keeps its first copy, as a feed that lists
    one twice still means one event.
    """
    objects = {}
    for uid, event in parsed:
        obj = objects.setdefault(uid, ParsedObject(uid=uid, master=None))
        if event.recurrence_id is None:
            if obj.master is None:
                obj.master = event
        elif all(o.recurrence_id != event.recurrence_id for o in obj.overrides):
            obj.overrides.append(event)
    return list(objects.values())


def _raw_vevents(lines, strict):
    """The content lines of each top-level VEVENT, BEGIN/END excluded.

    In a strict read only VEVENT and VTIMEZONE may sit in the VCALENDAR: a
    VTODO in an event calendar would be accepted and then silently dropped.
    A lenient read skips them.

    Every END must close the component its BEGIN opened, and no calendar,
    event or zone may open inside another component: the nested lines are
    stored and re-emitted verbatim, so an ``END:VEVENT`` hidden in an alarm
    would close the event early wherever it is served, and whoever sent it
    could append events of their own to the calendar.
    """
    blocks = []
    stack = []
    current = None
    for line in lines:
        if not line.strip():
            continue
        name = _property_name(line)
        value = line.split(":", 1)[1].strip().upper() if ":" in line else ""
        if name == "BEGIN":
            if strict and len(stack) == 1 and value not in ("VEVENT", "VTIMEZONE"):
                raise InvalidObject(
                    f"{value} components are not supported here.",
                    "supported-calendar-component",
                )
            if not stack and value != "VCALENDAR":
                raise InvalidObject("The body is not a VCALENDAR.")
            if len(stack) >= 2 and value in _TOP_LEVEL:
                raise InvalidObject(f"{value} cannot be nested here.")
            if len(stack) == 1 and value == "VEVENT":
                current = []
                blocks.append(current)
                stack.append(value)
                continue
            stack.append(value)
        elif name == "END":
            if not stack or stack[-1] != value:
                raise InvalidObject("Unbalanced END line.")
            stack.pop()
            if len(stack) == 1 and value == "VEVENT":
                current = None
                continue
        if current is not None:
            current.append(line)
    if stack:
        raise InvalidObject("Unterminated component.")
    return blocks


def _parse_vevent(vevent, raw, default_tz, strict):
    """``(uid, ParsedEvent)`` for one VEVENT."""
    try:
        uid = str(vevent.uid or "").strip()
        dtstart = vevent.DTSTART
        if dtstart is None and strict:
            raise InvalidObject(
                "An event needs a DTSTART.", "valid-calendar-object-resource"
            )
        start, tzid = _instant(dtstart, default_tz)
        all_day = dtstart is not None and not isinstance(dtstart, datetime)
        if vevent.DTEND is not None:
            end, _ = _instant(vevent.DTEND, default_tz)
        elif vevent.DURATION is not None and start is not None:
            end = start + vevent.DURATION
        else:
            end = None
        recurrence_id, _ = _instant(vevent.RECURRENCE_ID, default_tz)
        sequence = vevent.sequence
        cancelled = str(vevent.status or "").upper() == "CANCELLED"
        title = str(vevent.summary or "")
        description = str(vevent.description or "")
        location = str(vevent.location or "")
        organizer = vevent.organizer.email if vevent.organizer else ""
    except InvalidObject:
        raise
    except ValueError, TypeError, AttributeError:
        raise InvalidObject("An event property could not be read.") from None

    if all_day:
        start, end = normalize_all_day(start), normalize_all_day(end)
        tzid = ""
        if recurrence_id is not None:
            recurrence_id = normalize_all_day(recurrence_id)

    is_override = recurrence_id is not None
    rule, extra, rid_line = [], [], ""
    depth = 0
    for line in raw:
        name = _property_name(line)
        if depth or name == "BEGIN":
            depth += {"BEGIN": 1, "END": -1}.get(name, 0)
            extra.append(line)
            continue
        if name == "RECURRENCE-ID":
            rid_line = line
        elif name in _CONSUMED:
            continue
        elif name in _RECURRENCE and not is_override:
            rule.append(line)
        elif name == "STATUS" and cancelled:
            continue
        else:
            extra.append(line)

    event = ParsedEvent(
        title=title[:_MAX_TEXT],
        description=description,
        location=location[:_MAX_TEXT],
        start=start,
        end=end,
        all_day=all_day,
        timezone=tzid,
        sequence=sequence,
        cancelled=cancelled,
        recurrence_id=recurrence_id,
        rule="\n".join(rule),
        extra="\n".join(extra),
        organizer=organizer[:_MAX_EMAIL],
        recurrence_line=rid_line,
    )
    return uid, event


def _instant(value, default_tz):
    """``(aware UTC datetime, IANA zone name)`` for a DATE or DATE-TIME value.

    A DATE is a day label: UTC midnight, no zone. A floating DATE-TIME is "local
    time of the observer", read in *default_tz*. The zone name is "" for UTC
    and for a zone nothing names, which leaves the event on the UTC path.
    """
    if value is None:
        return None, ""
    if not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC), ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=default_tz)
    return value.astimezone(UTC), _zone_name(value.tzinfo)


def _zone_name(tzinfo):
    zone = zone_from_tzid(getattr(tzinfo, "key", None) or tzid_from_tzinfo(tzinfo))
    return "" if zone is None or zone.key == "UTC" else zone.key

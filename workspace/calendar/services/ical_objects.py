"""Calendar objects as iCalendar text, both directions, with no database involved.

A calendar object is what CalDAV serves as one ``.ics`` resource: a single
event, or a series master with its exception rows, as one VCALENDAR whose
VEVENTs share a UID (RFC 4791 4.1). The ICS feed renders every object of a
calendar into a single VCALENDAR the same way.

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
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar

from .ics_builder import add_event_times
from .ics_common import is_all_day, parse_dt_prop
from .timezones import event_timezone, normalize_all_day

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

_NAME_RE = re.compile(r"[A-Za-z0-9-]+")
_TZID_RE = re.compile(r';TZID=(?:"([^"]*)"|([^;:]*))', re.IGNORECASE)
_UNFOLD_RE = re.compile(r"\r?\n[ \t]")

# The width of the title and location columns.
_MAX_TEXT = 255

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

    verbatim = _lines(event.recurrence_rule) if event is master else []
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
    """One VEVENT of a PUT body, mapped onto Event columns."""

    title: str
    description: str
    location: str
    start: datetime
    end: datetime | None
    all_day: bool
    timezone: str
    sequence: int
    cancelled: bool
    recurrence_id: datetime | None = None
    rule: str = ""
    extra: str = ""


@dataclass
class ParsedObject:
    uid: str
    master: ParsedEvent
    overrides: list[ParsedEvent] = field(default_factory=list)


def parse_object(data, default_tz=UTC):
    """Read a PUT body into one calendar object, or raise InvalidObject.

    *default_tz* resolves floating times: "local time of the observer", and
    the calendar's owner is the closest observer there is.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidObject("The body is not UTF-8.") from exc

    raw_blocks = _raw_vevents(_UNFOLD_RE.sub("", data).splitlines())
    try:
        calendar = icalendar.Calendar.from_ical(data)
    except ValueError, IndexError, KeyError, TypeError:
        raise InvalidObject("The body is not an iCalendar object.") from None
    vevents = [c for c in calendar.subcomponents if c.name == "VEVENT"]
    if not vevents:
        raise InvalidObject(
            "The object holds no event.", "supported-calendar-component"
        )
    if len(vevents) != len(raw_blocks):
        raise InvalidObject("The events could not be read.")

    parsed = [
        _parse_vevent(vevent, raw, default_tz)
        for vevent, raw in zip(vevents, raw_blocks, strict=True)
    ]
    uids = {uid for uid, _event, _rid_line in parsed}
    if len(uids) != 1 or "" in uids:
        raise InvalidObject(
            "Every event of an object must carry the same UID.",
            "valid-calendar-object-resource",
        )

    masters = [event for _uid, event, _line in parsed if event.recurrence_id is None]
    overrides = [
        event for _uid, event, _line in parsed if event.recurrence_id is not None
    ]
    if len(masters) > 1:
        raise InvalidObject(
            "An object holds at most one series.", "valid-calendar-object-resource"
        )
    if not masters:
        if len(overrides) != 1:
            raise InvalidObject(
                "Occurrences without their series are not supported.",
                "valid-calendar-object-resource",
            )
        # One occurrence of somebody else's series (an invitation to a single
        # date): stored as a plain event, with its RECURRENCE-ID kept as an
        # extra line so it goes back to the client as it came.
        master = overrides.pop()
        master.extra = "\n".join([parsed[0][2], *_lines(master.extra)])
        master.recurrence_id = None
        masters = [master]
    return ParsedObject(uid=uids.pop(), master=masters[0], overrides=overrides)


def _raw_vevents(lines):
    """The content lines of each top-level VEVENT, BEGIN/END excluded.

    Only VEVENT and VTIMEZONE may sit in the VCALENDAR: a VTODO in an event
    calendar would be accepted and then silently dropped.
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
            if len(stack) == 1 and value not in ("VEVENT", "VTIMEZONE"):
                raise InvalidObject(
                    f"{value} components are not supported here.",
                    "supported-calendar-component",
                )
            if not stack and value != "VCALENDAR":
                raise InvalidObject("The body is not a VCALENDAR.")
            if len(stack) == 1 and value == "VEVENT":
                current = []
                blocks.append(current)
                stack.append(value)
                continue
            stack.append(value)
        elif name == "END":
            if not stack:
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


def _parse_vevent(vevent, raw, default_tz):
    """``(uid, ParsedEvent, raw RECURRENCE-ID line)`` for one VEVENT."""
    try:
        uid = str(vevent.get("UID", "")).strip()
        dtstart = vevent.get("DTSTART")
        if dtstart is None:
            raise InvalidObject(
                "An event needs a DTSTART.", "valid-calendar-object-resource"
            )
        start, tzid = parse_dt_prop(dtstart, default_tz)
        all_day = is_all_day(dtstart)
        if vevent.get("DTEND") is not None:
            end, _ = parse_dt_prop(vevent.get("DTEND"), default_tz)
        elif vevent.get("DURATION") is not None:
            end = start + vevent.get("DURATION").dt
        else:
            end = None
        recurrence_id = None
        if vevent.get("RECURRENCE-ID") is not None:
            recurrence_id, _ = parse_dt_prop(vevent.get("RECURRENCE-ID"), default_tz)
        sequence = int(vevent.get("SEQUENCE", 0))
        cancelled = str(vevent.get("STATUS", "")).upper() == "CANCELLED"
        title = str(vevent.get("SUMMARY", ""))
        description = str(vevent.get("DESCRIPTION", ""))
        location = str(vevent.get("LOCATION", ""))
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
    )
    return uid, event, rid_line

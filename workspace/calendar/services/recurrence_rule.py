"""Parse, derive from, describe and rewrite RFC 5545 recurrence rules.

``Event.recurrence_rule`` holds the client's recurrence lines verbatim. It is
the only faithful representation and the one calendar clients compare against,
so nothing in this module ever normalizes it on the way in.

``Event.is_recurring`` and ``Event.recurrence_until`` are derived from that text
purely so the calendar's two hot queries stay indexable. ``apply_rule`` is their
only writer; ``tests/test_recurrence_invariant.py`` fails the build on any
assignment to them elsewhere.

``icalendar`` is the only thing here that understands RFC 5545 syntax, reading
and writing alike: ``Contentline`` splits a property into its name, parameters
and value, ``vRecur`` reads an RRULE body into already-typed parts and writes
one back, ``vDDDLists`` reads an RDATE's values. Re-serializing an RRULE
reorders its parts (``FREQ;BYDAY;COUNT`` comes back as ``FREQ;COUNT;BYDAY`` -
identical meaning, different bytes), so a rule an operation does not have to
change is returned untouched rather than round-tripped: ``continue_after``
returns early when there is no COUNT to recount, and ``truncate_before`` leaves
every line but the ones it cuts alone. ``_normalize_for_parse`` is the one place
that reorders freely - its output goes straight to dateutil and is discarded.
"""

import logging
import re
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from icalendar.parser import Contentline
from icalendar.prop import vDDDLists, vRecur

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Occurrences to walk before abandoning an exact last-occurrence search. A
# daily series fires ~365 times a year, so this clears any real calendar while
# bounding a hostile FREQ=SECONDLY rule to a few milliseconds.
MAX_ITERATIONS = 10_000

_SIMPLE_FREQ = {
    "DAILY": "daily",
    "WEEKLY": "weekly",
    "MONTHLY": "monthly",
    "YEARLY": "yearly",
}
_SIMPLE_FREQ_INVERSE = {value: key for key, value in _SIMPLE_FREQ.items()}

# Steps the dtstart re-anchoring optimization can compute algebraically.
# Deliberately wider than _SIMPLE_FREQ (the web picker's set): HOURLY,
# MINUTELY and SECONDLY are the frequencies a dense feed-supplied rule most
# often uses and most need anchoring, even though the picker never offers
# them.
_FIXED_STEP_FREQ = {"DAILY", "WEEKLY", "HOURLY", "MINUTELY", "SECONDLY"}

_INTERVAL_RE = re.compile(r"(?:^|;)\s*INTERVAL\s*=([^;]*)", re.IGNORECASE)

_UNIT_LABELS = {
    "daily": ("day", "days"),
    "weekly": ("week", "weeks"),
    "monthly": ("month", "months"),
    "yearly": ("year", "years"),
}

# describe()'s BY-qualified branch: rules to_simple() rejects (a BYDAY, a
# BYMONTHDAY, a COUNT) but that still phrases into a short English sentence.
# Anything wider than this set - BYSETPOS, BYMONTH, BYWEEKNO, a second RRULE
# line, an RDATE - keeps returning the raw rule text, same as before.
_BY_QUALIFIED_KEYS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL"}

_WEEKDAY_SHORT = {
    "MO": "Mon",
    "TU": "Tue",
    "WE": "Wed",
    "TH": "Thu",
    "FR": "Fri",
    "SA": "Sat",
    "SU": "Sun",
}

_WEEKDAY_FULL = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}

_ORDINAL_LABELS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", -1: "last"}


def _ical_utc(value):
    """Format an aware datetime as an RFC 5545 UTC DATE-TIME."""
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _rule_lines(rule_text):
    return [line.strip() for line in rule_text.splitlines() if line.strip()]


def _contentline(line):
    """Split one content line into ``(NAME, parameters, value)``.

    ``RDATE;TZID=America/New_York:20260501T090000`` ->
    ``("RDATE", {"TZID": "America/New_York"}, "20260501T090000")``. None when
    icalendar cannot read the line at all, which every caller treats as "not a
    property I handle": text that malformed is reported once, by ``parse``.
    """
    try:
        name, params, value = Contentline(line).parts()
    except ValueError:
        return None
    return name.upper(), params, value


def _recur_parts(value):
    """Read an RRULE body into ``{"FREQ": ["DAILY"], "COUNT": [5]}``, or None.

    Every part is a list of already-typed values: COUNT and INTERVAL are ints,
    UNTIL is a date or a datetime, a BYDAY entry carries ``.relative`` and
    ``.weekday``. None means icalendar refused the body, which every reader
    treats like a shape it does not recognise.
    """
    try:
        return dict(vRecur.from_ical(value))
    except ValueError, TypeError:
        return None


def _rrule_parts(line):
    """``_recur_parts`` for a line that is exactly an unparameterized RRULE.

    None for anything else - another property, an RRULE carrying a parameter,
    an unreadable line, a body icalendar refuses - which is the answer the
    strict readers want for all four.
    """
    parsed = _contentline(line)
    if parsed is None:
        return None
    name, params, value = parsed
    if name != "RRULE" or params:
        return None
    return _recur_parts(value)


def _rrule_bodies(rule_text):
    """The value of every RRULE line in *rule_text*, in order."""
    bodies = []
    for line in _rule_lines(rule_text):
        parsed = _contentline(line)
        if parsed is not None and parsed[0] == "RRULE":
            bodies.append(parsed[2])
    return bodies


def _single(parts, key, default=None):
    """First value of a vRecur part, or *default* when the part is absent."""
    values = parts.get(key)
    return values[0] if values else default


def _zone_or_utc(tzid):
    """Resolve a TZID parameter to a zone, or UTC when missing/unrecognised."""
    if not tzid:
        return UTC
    try:
        return ZoneInfo(tzid)
    except ZoneInfoNotFoundError, ValueError:
        return UTC


def _zone_from_name(name):
    """Resolve an IANA zone name to a ZoneInfo, or None for the legacy UTC path.

    Mirrors ``timezones.event_timezone``'s contract but takes a bare name, so
    both ``apply_rule`` (which holds an ``Event``) and ``derive_into_defaults``
    (which holds only an ``update_or_create`` payload dict) resolve the same
    way instead of each growing their own copy of this.
    """
    if not name or name == "UTC":
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return None


def _until_instant(value, zone):
    """UTC instant for an ``UNTIL`` value in any RFC 5545 form.

    icalendar hands back an aware datetime for the ``Z`` form (already an
    instant), a naive one for a local DATE-TIME (a wall-clock time in *zone*)
    and a date for the DATE form. A bare DATE covers the whole day (RFC 5545
    3.3.10 requires that form when DTSTART is a DATE, which is what every
    client emits for a bounded all-day series), so it resolves to the last
    second of that day - the inclusive reading, and the only one that cannot
    place the bound before the series' true final occurrence.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=zone)
        return value.astimezone(UTC)
    return datetime.combine(value, time(23, 59, 59), tzinfo=zone).astimezone(UTC)


def _until_datetime(value):
    """An ``UNTIL`` value as an aware UTC datetime, midnight for a bare DATE.

    Deliberately not ``_until_instant``, which stretches a DATE to the last
    second of its day because it is computing a bound. This one feeds the
    picker and the English summary, where 00:00 is the honest reading.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.combine(value, time(), tzinfo=UTC)


def _value_instant(value, zone):
    """Aware instant for one date value icalendar has already typed."""
    if isinstance(value, tuple):
        # A PERIOD ("<start>/<end>" or "<start>/<duration>") recurs at its
        # start; the length is the occurrence's, not the series'.
        value = value[0]
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=zone)
    return datetime.combine(value, time(), tzinfo=zone)


def _date_list_instants(value, params, zone):
    """Aware instants for one RDATE/EXDATE property value.

    Covers every value form clients emit: a UTC DATE-TIME, a wall-clock
    DATE-TIME under a TZID parameter (or floating, resolved in *zone*), a bare
    DATE under ``VALUE=DATE``, and a PERIOD. Raises ValueError on a value
    icalendar cannot read.
    """
    text = value.strip().strip(",")
    if not text:
        return []
    value_zone = _zone_or_utc(params["TZID"]) if "TZID" in params else zone
    return [_value_instant(parsed, value_zone) for parsed in vDDDLists.from_ical(text)]


def _normalized_rrule_body(value, zone):
    """An RRULE body with its UNTIL rewritten as a UTC DATE-TIME.

    dateutil refuses any UNTIL that is not a UTC instant once dtstart is
    aware, which would reject the bounded all-day rules Google and Thunderbird
    emit. The result is handed to ``rrulestr`` and thrown away, so the part
    reordering re-serializing costs never reaches the stored column. A body
    that needs no rewrite is still returned untouched.
    """
    parts = _recur_parts(value)
    if parts is None:
        # rrulestr reports what it chokes on; its message beats ours.
        return value
    until = _single(parts, "UNTIL")
    if until is None or (isinstance(until, datetime) and until.tzinfo):
        return value
    parts["UNTIL"] = [_until_instant(until, zone)]
    return vRecur(parts).to_ical().decode()


def _normalize_for_parse(rule_text, zone):
    """Rewrite *rule_text* into the narrow dialect dateutil actually accepts.

    dateutil's ``rrulestr`` rejects every RDATE property parameter but
    ``VALUE=DATE-TIME``, and parses the remaining values with no timezone at
    all, so a floating RDATE comes back naive and blows up on the first
    comparison with an aware RRULE stream. Resolving every date value to a UTC
    instant here keeps the stored text verbatim while handing the engine
    something it can iterate.

    Raises ValueError on a value it cannot read, which ``parse`` turns into
    "not a series" plus a scrubbed log line - a rule silently missing half its
    dates would be worse than one that visibly does not recur.
    """
    out = []
    for line in _rule_lines(rule_text):
        parsed = _contentline(line)
        if parsed is None:
            out.append(line)
            continue
        prop, params, value = parsed
        if prop in ("RRULE", "EXRULE"):
            out.append(f"{prop}:{_normalized_rrule_body(value, zone)}")
        elif prop in ("RDATE", "EXDATE"):
            instants = _date_list_instants(value, params, zone)
            if instants:
                out.append(f"{prop}:" + ",".join(_ical_utc(dt) for dt in instants))
        else:
            out.append(line)
    return "\n".join(out)


def _has_non_positive_interval(rule_text):
    """True when any RRULE carries an INTERVAL that is not a positive integer.

    RFC 5545 3.3.10 requires INTERVAL to be a positive integer, but dateutil
    accepts zero and negatives. Zero is the dangerous one: the anchoring
    optimization multiplies the frequency's step by the interval and divides
    the pre-window gap by it, so INTERVAL=0 raises ZeroDivisionError out of
    the expansion rather than degrading. Rejecting it here, where every read
    already passes, keeps that failure a non-series instead of a 500.

    Reads the literal rather than icalendar's typed value on purpose:
    ``vRecur.from_ical`` refuses a body as a whole, so ``INTERVAL=x`` would
    come back as "this rule is malformed" with no way to tell it from a broken
    FREQ - and those two deserve different warnings.
    """
    for body in _rrule_bodies(rule_text):
        for raw in _INTERVAL_RE.findall(body):
            try:
                if int(raw) < 1:
                    return True
            except ValueError:
                return True
    return False


def parse(rule_text, dtstart, tz=None):
    """Return a dateutil rruleset for *rule_text* anchored at *dtstart*.

    Returns None for a blank or unparseable rule. Callers treat None as "not a
    series": a malformed rule stored years ago must never 500 a calendar view.

    ``rrulestr`` builds an rruleset without checking that its parts are
    comparable, so a set that raises does so on the first iteration - far from
    here, inside whatever view happened to expand it. One occurrence is pulled
    under the guard to move that failure back to where it can still degrade.
    """
    if not rule_text:
        return None
    if _has_non_positive_interval(rule_text):
        logger.warning(
            "Unparseable recurrence rule %s: INTERVAL must be a positive integer",
            scrub(rule_text),
        )
        return None
    anchor = dtstart.astimezone(tz) if tz else dtstart
    zone = anchor.tzinfo or UTC
    try:
        rule = rrulestr(
            _normalize_for_parse(rule_text, zone), dtstart=anchor, forceset=True
        )
        next(iter(rule), None)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        logger.warning(
            "Unparseable recurrence rule %s: %s", scrub(rule_text), scrub(str(exc))
        )
        return None
    return rule


def _is_bounded(rule_text):
    """True when every RRULE line carries an UNTIL or a COUNT.

    One unbounded RRULE makes the whole set infinite however bounded its
    siblings are, so this is an ``all`` and not an ``any``.
    """
    bodies = _rrule_bodies(rule_text)
    if not bodies:
        # RDATE-only sets are finite by construction.
        return True
    for body in bodies:
        parts = _recur_parts(body)
        # A body icalendar refuses is not a series at all, and ``parse`` says
        # so a line later; reporting it unbounded reaches that answer without
        # inventing a bound of its own.
        if parts is None or not ("UNTIL" in parts or "COUNT" in parts):
            return False
    return True


def _rdate_instants(rule_text, zone):
    """Every RDATE instant in *rule_text*, or None if any of them is unreadable.

    An RDATE sits outside the RRULE stream, so it is free to fall after every
    UNTIL in the text - which makes it part of the bound rather than noise.
    """
    instants = []
    for line in _rule_lines(rule_text):
        parsed = _contentline(line)
        if parsed is None or parsed[0] != "RDATE":
            continue
        try:
            instants.extend(_date_list_instants(parsed[2], parsed[1], zone))
        except ValueError:
            return None
    return instants


def _loose_bound(rule_text, duration, zone=UTC):
    """Loosest safe bound when exact iteration was abandoned.

    Only safe when every RRULE line carries a literal UNTIL: a COUNT-only
    line's true last occurrence can fall well past the other lines' UNTIL
    values, and that can't be known without walking it - which is exactly
    what this function exists to avoid. So a mix of UNTIL and COUNT-only
    lines is reported as infinite (unbounded), not the max of the UNTIL
    literals present. That costs prune tightness, never correctness: it can
    only make the bound looser than the truth, never tighter.

    RDATEs are folded in the same way: one later than every UNTIL is the true
    last occurrence, and a bound that ignored it would sit *before* the end of
    the series - the one direction that silently deletes live events from a
    calendar view.
    """
    candidates = []
    for body in _rrule_bodies(rule_text):
        parts = _recur_parts(body)
        if parts is None or "UNTIL" not in parts:
            return None
        candidates.extend(_until_instant(value, zone) for value in parts["UNTIL"])
    rdates = _rdate_instants(rule_text, zone)
    if rdates is None:
        return None
    candidates.extend(dt.astimezone(UTC) for dt in rdates)
    if not candidates:
        return None
    latest = max(candidates)
    return latest + duration if duration else latest


def last_occurrence_end(rule_text, dtstart, duration=None, tz=None):
    """End instant of the series' final occurrence, or None when infinite.

    Deliberately the END of the last occurrence, not its start: the value is an
    upper bound for "can this series still overlap a window starting at X?",
    and an occurrence beginning before X can still run into it.
    """
    if not rule_text or not _is_bounded(rule_text):
        return None
    rule = parse(rule_text, dtstart, tz)
    if rule is None:
        return None
    anchor = dtstart.astimezone(tz) if tz else dtstart
    zone = anchor.tzinfo or UTC
    last = None
    try:
        for index, occurrence in enumerate(rule):
            if index >= MAX_ITERATIONS:
                return _loose_bound(rule_text, duration, zone)
            last = occurrence
    except (ValueError, TypeError, OverflowError) as exc:
        # No bound is the safe answer: recurrence_until=None never prunes.
        logger.warning(
            "Unwalkable recurrence rule %s: %s", scrub(rule_text), scrub(str(exc))
        )
        return None
    if last is None:
        return None
    if duration:
        last = last + duration
    return last.astimezone(UTC)


def from_simple(frequency, interval=1, until=None):
    """Compose rule text from the web picker's three fields."""
    if not frequency:
        return ""
    parts = [f"FREQ={_SIMPLE_FREQ_INVERSE[frequency]}"]
    if interval and int(interval) > 1:
        parts.append(f"INTERVAL={int(interval)}")
    if until:
        parts.append(f"UNTIL={_ical_utc(until)}")
    return "RRULE:" + ";".join(parts)


def to_simple(rule_text):
    """Picker-shaped view of *rule_text*, or None when it cannot be expressed.

    None is what puts the web UI into read-only mode, so this errs strict:
    anything beyond FREQ/INTERVAL/UNTIL - a BYDAY, a COUNT, an RDATE, a second
    RRULE - disqualifies the rule rather than being silently dropped the next
    time someone saves the event from the web modal.
    """
    if not rule_text:
        return None
    lines = _rule_lines(rule_text)
    if len(lines) != 1:
        return None
    parts = _rrule_parts(lines[0])
    if parts is None or set(parts) - {"FREQ", "INTERVAL", "UNTIL"}:
        return None
    frequency = _SIMPLE_FREQ.get(_single(parts, "FREQ", ""))
    if frequency is None:
        return None
    until = _single(parts, "UNTIL")
    return {
        "frequency": frequency,
        "interval": int(_single(parts, "INTERVAL", 1)),
        "until": _until_datetime(until) if until is not None else None,
    }


def to_simple_json(rule_text):
    """`to_simple`, shaped for a JSON response - or None, same as `to_simple`.

    Single definition for the picker payload shape (frequency/interval/until
    as an ISO string) shared by EventSerializer and the occurrence-dict
    builders in recurrence.py, so the two callers can't drift apart.
    """
    simple = to_simple(rule_text)
    if simple is None:
        return None
    return {
        "frequency": simple["frequency"],
        "interval": simple["interval"],
        "until": simple["until"].isoformat() if simple["until"] else None,
    }


def _until_phrase(until):
    """ ", until 1 March 2026" for an aware UTC datetime.

    Shared by the FREQ/INTERVAL/UNTIL branch and the BY-qualified branch so
    the two never drift into two different UNTIL phrasings.
    """
    return f", until {until.day} {until.strftime('%B %Y')}"


def _count_phrase(count):
    """ ", for 10 occurrences"."""
    return f", for {count} occurrence{'' if count == 1 else 's'}"


def _by_qualified_tail(parts):
    """The ", for N occurrences" / ", until ..." suffix, or "" for neither.

    RFC 5545 forbids COUNT and UNTIL on the same RRULE line, so a rule
    carrying both is malformed; None tells the caller to fall back to the
    raw text rather than silently picking one.
    """
    if "COUNT" in parts and "UNTIL" in parts:
        return None
    if "COUNT" in parts:
        return _count_phrase(_single(parts, "COUNT"))
    if "UNTIL" in parts:
        return _until_phrase(_until_datetime(_single(parts, "UNTIL")))
    return ""


def _describe_byday(freq, interval, weekdays):
    """Phrase a BYDAY clause, or None outside the two shapes this covers.

    A single ordinal weekday (``2TU``) reads as "Every 2nd Tuesday of the
    month" and only makes sense against FREQ=MONTHLY - dateutil accepts an
    ordinal BYDAY on a YEARLY rule too, but that needs a BYMONTH alongside it
    to mean anything ("the 2nd Tuesday of March"), which is outside what this
    phrases. A weekday set with no ordinals (``MO,WE,FR``) reads as "Every
    week on Mon, Wed and Fri" and only makes sense against FREQ=WEEKLY.
    """
    tokens = [(day.relative, day.weekday) for day in weekdays]
    if not tokens:
        return None

    if len(tokens) == 1 and tokens[0][0] is not None:
        if freq != "MONTHLY":
            return None
        ordinal, weekday = tokens[0]
        label = _ORDINAL_LABELS.get(ordinal)
        if label is None:
            return None
        weekday_name = _WEEKDAY_FULL[weekday]
        phrase = f"Every {label} {weekday_name} of the month"
        if interval > 1:
            phrase += f" every {interval} months"
        return phrase

    if any(ordinal is not None for ordinal, _ in tokens):
        # A mixed or multi-entry ordinal set ("1MO,3MO") isn't one this
        # phrases; the raw text beats a confident mistranslation.
        return None
    if freq != "WEEKLY":
        return None

    labels = [_WEEKDAY_SHORT[weekday] for _, weekday in tokens]
    if len(labels) == 1:
        joined = labels[0]
    else:
        joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    singular, plural = _UNIT_LABELS["weekly"]
    prefix = f"Every {singular}" if interval == 1 else f"Every {interval} {plural}"
    return f"{prefix} on {joined}"


def _describe_bymonthday(freq, interval, days):
    """Phrase a single-day BYMONTHDAY clause, or None otherwise.

    Only a lone day number is covered ("Monthly on day 15", -1 as "on the
    last day") - a list of days ("15,20") is outside what this phrases.
    """
    if freq != "MONTHLY" or len(days) != 1:
        return None
    day = days[0]
    if day == -1:
        day_phrase = "on the last day"
    elif 1 <= day <= 31:
        day_phrase = f"on day {day}"
    else:
        return None
    if interval == 1:
        return f"Monthly {day_phrase}"
    return f"Every {interval} months {day_phrase}"


def _describe_by_qualified(rule_text):
    """describe()'s second attempt: rules to_simple() rejects that still
    phrase into English - an ordinal or weekday-set BYDAY, or a single-day
    BYMONTHDAY, each optionally closed with a COUNT or UNTIL tail.

    Returns None for anything wider (BYSETPOS, BYMONTH, a second RRULE line,
    an RDATE, ...), which tells describe() to fall back to the raw text.
    """
    lines = _rule_lines(rule_text)
    if len(lines) != 1:
        return None
    parts = _rrule_parts(lines[0])
    if parts is None or set(parts) - _BY_QUALIFIED_KEYS:
        return None
    if "BYDAY" in parts and "BYMONTHDAY" in parts:
        return None
    freq = _single(parts, "FREQ", "")
    interval = int(_single(parts, "INTERVAL", 1))

    if "BYDAY" in parts:
        body = _describe_byday(freq, interval, parts["BYDAY"])
    elif "BYMONTHDAY" in parts:
        body = _describe_bymonthday(freq, interval, parts["BYMONTHDAY"])
    else:
        return None
    if body is None:
        return None

    tail = _by_qualified_tail(parts)
    if tail is None:
        return None
    return body + tail


def describe(rule_text):
    """Human summary of *rule_text*, falling back to the raw text.

    Tries the picker's FREQ/INTERVAL/UNTIL shape first, then the wider set
    of BY-qualified shapes real clients emit most (an ordinal or weekday-set
    BYDAY, a single-day BYMONTHDAY, a COUNT or UNTIL tail on either).
    Anything past that is returned verbatim: the user sees a rule they
    cannot edit here rather than a confident mistranslation - and it stays
    unconnected to to_simple(), which must keep rejecting all of it so the
    picker never overwrites a rule it cannot represent.
    """
    simple = to_simple(rule_text)
    if simple is not None:
        singular, plural = _UNIT_LABELS[simple["frequency"]]
        interval = simple["interval"]
        summary = f"Every {singular}" if interval == 1 else f"Every {interval} {plural}"
        if simple["until"]:
            summary += _until_phrase(simple["until"])
        return summary

    described = _describe_by_qualified(rule_text)
    return described if described is not None else rule_text


def continue_after(rule_text, dtstart, instant, tz=None):
    """Return *rule_text* rewritten for the second half of a series split.

    Only COUNT needs rewriting. An UNTIL rule, or an unbounded one, re-anchored
    at the split point already yields exactly the remaining tail. A COUNT
    restarts its tally from the new anchor instead, so splitting COUNT=10 after
    three occurrences would leave 3 + 10 rather than 10.

    The recount walks the original series, so it only runs once every RRULE is
    bounded. A COUNT on one line does not bound the set: a rule pairing
    ``FREQ=DAILY;COUNT=3`` with a bare ``FREQ=WEEKLY`` contains the text
    ``COUNT=`` while recurring forever, and counting it would never return.
    ``_is_bounded`` asks the question the substring cannot.

    A rule with nothing to recount comes back byte-identical: the early return
    is what keeps an UNTIL or unbounded rule out of the re-serializer, which
    would reorder its parts for no gain.
    """
    if "COUNT=" not in rule_text.upper() or not _is_bounded(rule_text):
        return rule_text
    rule = parse(rule_text, dtstart, tz)
    if rule is None:
        return rule_text

    remaining = 0
    for index, occurrence in enumerate(rule):
        # Second guard, in case a rule reads bounded but walks like it is not:
        # leaving the COUNT alone costs a longer continuation, hanging the
        # request costs the worker.
        if index >= MAX_ITERATIONS:
            return rule_text
        if occurrence >= instant:
            remaining += 1
    remaining = max(remaining, 1)

    out = []
    for line in _rule_lines(rule_text):
        name, _, _ = line.partition(":")
        parsed = _contentline(line)
        parts = _recur_parts(parsed[2]) if parsed and parsed[0] == "RRULE" else None
        if parts is None or "COUNT" not in parts:
            out.append(line)
            continue
        parts["COUNT"] = [remaining]
        out.append(f"{name}:" + vRecur(parts).to_ical().decode())
    return "\n".join(out)


def truncate_before(rule_text, instant):
    """Return *rule_text* rewritten so the series stops at or before *instant*.

    Used by the "this and all following occurrences" split. UNTIL is inclusive
    per RFC 5545, so the caller passes the last instant to KEEP, not the first
    to drop - see ``event_scope._truncate_series``, which subtracts a second
    from the split point.

    COUNT is dropped wherever it appears: RFC 5545 forbids COUNT and UNTIL in
    the same RRULE, so a counted series has to be re-expressed as a dated one
    to be truncatable at all, and appending an UNTIL beside a COUNT produces a
    rule clients reject.
    """
    out = []
    for line in _rule_lines(rule_text):
        name, _, body = line.partition(":")
        parsed = _contentline(line)
        prop, params = (parsed[0], parsed[1]) if parsed else ("", {})
        parts = _recur_parts(parsed[2]) if prop == "RRULE" else None
        if parts is not None:
            parts.pop("COUNT", None)
            parts["UNTIL"] = [instant]
            out.append(f"{name}:" + vRecur(parts).to_ical().decode())
        elif prop == "RRULE":
            # A body icalendar cannot read is a body ``parse`` refuses too, so
            # there is no series here to stop. A second bound beside the
            # broken one would only make the text harder to repair.
            out.append(line)
        elif prop in ("RDATE", "EXDATE"):
            # Rebuilt from the line's own name-and-parameters segment: a
            # TZID says the values are local wall-clock times, so dropping it
            # would move every surviving date by the zone's offset. It also
            # means they carry no trailing Z, so they are resolved in that
            # zone before comparing to *instant*, a UTC-anchored cutoff.
            zone = _zone_or_utc(params.get("TZID"))
            surviving = []
            for value in body.split(","):
                try:
                    # A PERIOD value ("<start>/<end>") is compared on its
                    # start; anything else is a bare DATE or DATE-TIME.
                    occurrence = _value_instant(
                        vDDDLists.from_ical(value.strip())[0], zone
                    )
                except ValueError, IndexError:
                    # Unreadable here means unreadable to parse() too, so the
                    # value fires nothing. Keep it rather than silently
                    # deleting a date the user wrote.
                    surviving.append(value)
                    continue
                if occurrence < instant:
                    surviving.append(value)
            if surviving:
                out.append(f"{name}:" + ",".join(surviving))
        else:
            out.append(line)
    return "\n".join(out)


def is_simple_stepping(rule_text):
    """True when the series advances by a fixed timedelta.

    Gates the dtstart re-anchoring optimization in ``recurrence.py``, whose
    algebra assumes a constant step. BYDAY sets, calendar-dependent monthly and
    yearly stepping, extra RDATEs, and COUNT all break that assumption: moving
    dtstart forward and keeping the same COUNT would fabricate occurrences past
    the series' real end, since COUNT is measured from dtstart.
    """
    lines = _rule_lines(rule_text)
    if len(lines) != 1:
        return False
    parts = _rrule_parts(lines[0])
    if parts is None or _single(parts, "FREQ", "") not in _FIXED_STEP_FREQ:
        return False
    if "COUNT" in parts:
        return False
    return not any(key.startswith("BY") for key in parts)


def simple_stepping_frequency(rule_text):
    """Return ``(frequency, interval)`` for a rule ``is_simple_stepping``
    has already accepted, or ``None`` if INTERVAL isn't a valid integer.

    Callers must check ``is_simple_stepping`` first. Deliberately decoupled
    from ``to_simple``: the dtstart re-anchoring optimization in
    ``recurrence.py`` needs HOURLY/MINUTELY/SECONDLY too, which the web
    picker's ``to_simple`` never expresses and must keep never expressing -
    the two functions answer different questions ("can this be anchored?"
    vs. "can this be rendered in the three-field picker?") and only look
    alike for the frequencies where the answer happens to agree.
    """
    parts = _rrule_parts(_rule_lines(rule_text)[0])
    if parts is None:
        return None
    return _single(parts, "FREQ", "").lower(), int(_single(parts, "INTERVAL", 1))


def apply_rule(event, rule_text):
    """Set the authoritative rule on *event* and re-derive its index columns.

    The single writer of ``is_recurring`` and ``recurrence_until``. Every write
    path calls it; the structural test fails the build on any other assignment.
    Does not save - the caller owns the transaction.
    """
    event.recurrence_rule = rule_text or ""
    zone = _zone_from_name(event.timezone)
    # Text nothing can parse is not a series: marking it one leaves a master
    # with no bound, which no window query can ever prune, on every read.
    event.is_recurring = parse(event.recurrence_rule, event.start, zone) is not None
    if event.is_recurring:
        duration = (event.end - event.start) if event.end else None
        event.recurrence_until = last_occurrence_end(
            event.recurrence_rule,
            event.start,
            duration,
            zone,
        )
    else:
        event.recurrence_until = None


def derive_into_defaults(defaults):
    """Apply the same derivation to an ``update_or_create`` defaults dict.

    ``ics_sync`` upserts rows without ever holding an instance, so it cannot
    call ``apply_rule``. Keeping this here rather than inline at the call site
    is what lets the structural test insist the derived columns are only ever
    assigned in this module.
    """
    rule = defaults.get("recurrence_rule", "")
    start, end = defaults.get("start"), defaults.get("end")
    duration = (end - start) if start and end else None
    zone = _zone_from_name(defaults.get("timezone"))
    recurring = bool(start) and parse(rule, start, zone) is not None
    defaults["is_recurring"] = recurring
    defaults["recurrence_until"] = (
        last_occurrence_end(rule, start, duration, zone) if recurring else None
    )

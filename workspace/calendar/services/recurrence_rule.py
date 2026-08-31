"""Parse, derive from, describe and rewrite RFC 5545 recurrence rules.

``Event.recurrence_rule`` holds the client's recurrence lines verbatim. It is
the only faithful representation and the one calendar clients compare against,
so nothing in this module ever normalizes it on the way in.

``Event.is_recurring`` and ``Event.recurrence_until`` are derived from that text
purely so the calendar's two hot queries stay indexable. ``apply_rule`` is their
only writer; ``tests/test_recurrence_invariant.py`` fails the build on any
assignment to them elsewhere.
"""

import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

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

_UNTIL_RE = re.compile(r"UNTIL=(\d{8}(?:T\d{6}Z?)?)")

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

_BYDAY_TOKEN_RE = re.compile(r"^(-?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")

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


def _parse_ical_utc(text, zone=UTC):
    """Parse an RFC 5545 DATE or DATE-TIME into an aware datetime.

    A trailing ``Z`` always means UTC, regardless of *zone*. Without one the
    value is a local wall-clock time in *zone* - an RDATE/EXDATE under a TZID
    parameter, per RFC 5545 3.8.5.2/3.8.5.3.
    """
    if text.endswith("Z"):
        text = text[:-1]
        zone = UTC
    fmt = "%Y%m%dT%H%M%S" if "T" in text else "%Y%m%d"
    return datetime.strptime(text, fmt).replace(tzinfo=zone)


def _rule_lines(rule_text):
    return [line.strip() for line in rule_text.splitlines() if line.strip()]


def _properties(line):
    """Split ``NAME:a=1;b=2`` into ``("NAME", {"A": "1", "B": "2"})``."""
    name, _, body = line.partition(":")
    parts = {}
    for token in body.split(";"):
        key, _, value = token.partition("=")
        parts[key.upper()] = value
    return name.upper(), parts


def _name_and_params(name):
    """Split a property's name segment into its base name and parameters.

    ``RDATE;TZID=America/New_York`` -> (``"RDATE"``, {"TZID": "America/New_York"}).
    Property parameters live before the colon, so this is distinct from
    ``_properties``, which parses the value pairs after it.
    """
    base, *param_tokens = name.split(";")
    params = {}
    for token in param_tokens:
        key, _, value = token.partition("=")
        params[key.upper()] = value
    return base.upper(), params


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


def parse(rule_text, dtstart, tz=None):
    """Return a dateutil rruleset for *rule_text* anchored at *dtstart*.

    Returns None for a blank or unparseable rule. Callers treat None as "not a
    series": a malformed rule stored years ago must never 500 a calendar view.
    """
    if not rule_text:
        return None
    anchor = dtstart.astimezone(tz) if tz else dtstart
    try:
        return rrulestr(rule_text, dtstart=anchor, forceset=True)
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "Unparseable recurrence rule %s: %s", scrub(rule_text), scrub(str(exc))
        )
        return None


def _is_bounded(rule_text):
    """True when every RRULE line carries an UNTIL or a COUNT.

    One unbounded RRULE makes the whole set infinite however bounded its
    siblings are, so this is an ``all`` and not an ``any``.
    """
    rules = [ln for ln in _rule_lines(rule_text) if ln.upper().startswith("RRULE")]
    if not rules:
        # RDATE-only sets are finite by construction.
        return True
    return all("UNTIL=" in ln.upper() or "COUNT=" in ln.upper() for ln in rules)


def _loose_bound(rule_text, duration):
    """Loosest safe bound when exact iteration was abandoned.

    Only safe when every RRULE line carries a literal UNTIL: a COUNT-only
    line's true last occurrence can fall well past the other lines' UNTIL
    values, and that can't be known without walking it - which is exactly
    what this function exists to avoid. So a mix of UNTIL and COUNT-only
    lines is reported as infinite (unbounded), not the max of the UNTIL
    literals present. That costs prune tightness, never correctness: it can
    only make the bound looser than the truth, never tighter.
    """
    rules = [ln for ln in _rule_lines(rule_text) if ln.upper().startswith("RRULE")]
    if not all("UNTIL=" in ln.upper() for ln in rules):
        return None
    untils = _UNTIL_RE.findall(rule_text.upper())
    if not untils:
        return None
    latest = max(_parse_ical_utc(value) for value in untils)
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
    last = None
    for index, occurrence in enumerate(rule):
        if index >= MAX_ITERATIONS:
            return _loose_bound(rule_text, duration)
        last = occurrence
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
    name, parts = _properties(lines[0])
    if name != "RRULE" or set(parts) - {"FREQ", "INTERVAL", "UNTIL"}:
        return None
    frequency = _SIMPLE_FREQ.get(parts.get("FREQ", "").upper())
    if frequency is None:
        return None
    try:
        interval = int(parts.get("INTERVAL", 1))
        until = _parse_ical_utc(parts["UNTIL"]) if "UNTIL" in parts else None
    except ValueError:
        return None
    return {"frequency": frequency, "interval": interval, "until": until}


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


def _count_phrase(count_text):
    """ ", for 10 occurrences" - or None when *count_text* isn't an integer."""
    try:
        count = int(count_text)
    except ValueError:
        return None
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
        return _count_phrase(parts["COUNT"])
    if "UNTIL" in parts:
        try:
            until = _parse_ical_utc(parts["UNTIL"])
        except ValueError:
            return None
        return _until_phrase(until)
    return ""


def _parse_byday_tokens(value):
    """``"2TU,3WE"`` -> ``[(2, "TU"), (3, "WE")]``, or None if any token is
    outside the RFC 5545 ``[+-]ordwk`` grammar this function accepts."""
    tokens = []
    for raw in value.split(","):
        match = _BYDAY_TOKEN_RE.match(raw.strip().upper())
        if not match:
            return None
        ordinal_text, weekday = match.groups()
        tokens.append((int(ordinal_text) if ordinal_text else None, weekday))
    return tokens


def _describe_byday(freq, interval, value):
    """Phrase a BYDAY clause, or None outside the two shapes this covers.

    A single ordinal weekday (``2TU``) reads as "Every 2nd Tuesday of the
    month" and only makes sense against FREQ=MONTHLY - dateutil accepts an
    ordinal BYDAY on a YEARLY rule too, but that needs a BYMONTH alongside it
    to mean anything ("the 2nd Tuesday of March"), which is outside what this
    phrases. A weekday set with no ordinals (``MO,WE,FR``) reads as "Every
    week on Mon, Wed and Fri" and only makes sense against FREQ=WEEKLY.
    """
    tokens = _parse_byday_tokens(value)
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


def _describe_bymonthday(freq, interval, value):
    """Phrase a single-day BYMONTHDAY clause, or None otherwise.

    Only a lone day number is covered ("Monthly on day 15", -1 as "on the
    last day") - a list of days ("15,20") is outside what this phrases.
    """
    if freq != "MONTHLY":
        return None
    tokens = [token.strip() for token in value.split(",")]
    if len(tokens) != 1:
        return None
    try:
        day = int(tokens[0])
    except ValueError:
        return None
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
    name, parts = _properties(lines[0])
    if name != "RRULE" or set(parts) - _BY_QUALIFIED_KEYS:
        return None
    if "BYDAY" in parts and "BYMONTHDAY" in parts:
        return None
    freq = parts.get("FREQ", "").upper()
    try:
        interval = int(parts.get("INTERVAL", 1))
    except ValueError:
        return None

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


def truncate_before(rule_text, instant):
    """Return *rule_text* rewritten so the series stops before *instant*.

    Used by the "this and all following occurrences" split. COUNT is dropped
    wherever it appears: RFC 5545 forbids COUNT and UNTIL in the same RRULE, so
    a counted series has to be re-expressed as a dated one to be truncatable at
    all, and appending an UNTIL beside a COUNT produces a rule clients reject.
    """
    until_token = f"UNTIL={_ical_utc(instant)}"
    out = []
    for line in _rule_lines(rule_text):
        name, _, body = line.partition(":")
        prop, params = _name_and_params(name)
        if prop == "RRULE":
            kept = [
                token
                for token in body.split(";")
                if token.split("=")[0].upper() not in ("UNTIL", "COUNT")
            ]
            kept.append(until_token)
            out.append(f"{name}:" + ";".join(kept))
        elif prop in ("RDATE", "EXDATE"):
            # A TZID parameter means the values are local wall-clock times
            # with no trailing Z; resolve them in that zone before comparing
            # to *instant*, which is a UTC-anchored cutoff.
            zone = _zone_or_utc(params.get("TZID"))
            surviving = [
                value
                for value in body.split(",")
                if _parse_ical_utc(value.split(";")[-1], zone) < instant
            ]
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
    name, parts = _properties(lines[0])
    if name != "RRULE" or parts.get("FREQ", "").upper() not in _FIXED_STEP_FREQ:
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
    _, parts = _properties(_rule_lines(rule_text)[0])
    try:
        interval = int(parts.get("INTERVAL", 1))
    except ValueError:
        return None
    return parts["FREQ"].lower(), interval


def apply_rule(event, rule_text):
    """Set the authoritative rule on *event* and re-derive its index columns.

    The single writer of ``is_recurring`` and ``recurrence_until``. Every write
    path calls it; the structural test fails the build on any other assignment.
    Does not save - the caller owns the transaction.
    """
    event.recurrence_rule = rule_text or ""
    event.is_recurring = bool(event.recurrence_rule)
    if event.is_recurring:
        duration = (event.end - event.start) if event.end else None
        event.recurrence_until = last_occurrence_end(
            event.recurrence_rule,
            event.start,
            duration,
            _zone_from_name(event.timezone),
        )
    else:
        event.recurrence_until = None

    # Transitional: the query layer and the expansion engine still read the
    # pre-rule columns, so every write keeps them in step. A rule they cannot
    # express blanks them - the row stays recurring by `is_recurring`, it is
    # just invisible to the legacy readers until they are migrated. This block
    # and the columns go together in the final task.
    simple = to_simple(event.recurrence_rule)
    event.recurrence_frequency = simple["frequency"] if simple else None
    event.recurrence_interval = simple["interval"] if simple else 1
    event.recurrence_end = simple["until"] if simple else None


def derive_into_defaults(defaults):
    """Apply the same derivation to an ``update_or_create`` defaults dict.

    ``ics_sync`` upserts rows without ever holding an instance, so it cannot
    call ``apply_rule``. Keeping this here rather than inline at the call site
    is what lets the structural test insist the derived columns are only ever
    assigned in this module.
    """
    rule = defaults.get("recurrence_rule", "")
    defaults["is_recurring"] = bool(rule)
    start, end = defaults.get("start"), defaults.get("end")
    duration = (end - start) if start and end else None
    zone = _zone_from_name(defaults.get("timezone"))
    defaults["recurrence_until"] = (
        last_occurrence_end(rule, start, duration, zone) if rule and start else None
    )

    # Transitional: mirror into the pre-rule columns, same as apply_rule.
    simple = to_simple(rule)
    defaults["recurrence_frequency"] = simple["frequency"] if simple else None
    defaults["recurrence_interval"] = simple["interval"] if simple else 1
    defaults["recurrence_end"] = simple["until"] if simple else None

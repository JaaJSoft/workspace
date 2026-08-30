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
_FIXED_STEP_FREQ = {"DAILY", "WEEKLY"}

_UNTIL_RE = re.compile(r"UNTIL=(\d{8}(?:T\d{6}Z?)?)")

_UNIT_LABELS = {
    "daily": ("day", "days"),
    "weekly": ("week", "weeks"),
    "monthly": ("month", "months"),
    "yearly": ("year", "years"),
}


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


def describe(rule_text):
    """Human summary of *rule_text*, falling back to the raw text.

    Covers what the picker can express plus the shapes real clients emit most.
    Anything else is returned verbatim: the user sees a rule they cannot edit
    here rather than a confident mistranslation.
    """
    simple = to_simple(rule_text)
    if simple is None:
        return rule_text
    singular, plural = _UNIT_LABELS[simple["frequency"]]
    interval = simple["interval"]
    summary = f"Every {singular}" if interval == 1 else f"Every {interval} {plural}"
    if simple["until"]:
        summary += f", until {simple['until'].date().isoformat()}"
    return summary


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
    yearly stepping, and extra RDATEs all break that assumption.
    """
    lines = _rule_lines(rule_text)
    if len(lines) != 1:
        return False
    name, parts = _properties(lines[0])
    if name != "RRULE" or parts.get("FREQ", "").upper() not in _FIXED_STEP_FREQ:
        return False
    return not any(key.startswith("BY") for key in parts)


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

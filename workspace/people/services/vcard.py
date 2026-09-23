"""vCard <-> Person mapping, both directions, with no database involved.

A card's mapped properties fill the person columns; every other property is
kept in ``extra_properties`` as ``{NAME: [{value, params, group}]}``, the
value in its wire form (escaped, base64 for binary), so a re-export emits it
byte for byte. ``KIND:group`` cards (and the Apple
``X-ADDRESSBOOKSERVER-KIND:group`` form) describe contact lists.

vobject only tokenises on the way in: its property decoders split a text
value on unescaped commas, which truncates a 4.0 ``PHOTO`` data URI at the
comma before the payload. Reading the raw content lines and decoding the
handful of mapped properties here sidesteps that.
"""

import base64
import datetime
import quopri
import re
from dataclasses import dataclass, field
from io import StringIO

import phonenumbers
import vobject
from vobject.base import (
    ContentLine,
    VObjectError,
    getLogicalLines,
    textLineToContentLine,
)
from vobject.vcard import splitFields

# Properties with a column of their own, or metadata that describes the file
# rather than the person: none of them belongs in ``extra_properties``.
_CONSUMED = frozenset(
    {
        "VERSION",
        "PRODID",
        "REV",
        "FN",
        "N",
        "ORG",
        "TITLE",
        "BDAY",
        "EMAIL",
        "TEL",
        "ADR",
        "NOTE",
        "UID",
        "KIND",
        "MEMBER",
        "X-ADDRESSBOOKSERVER-KIND",
        "X-ADDRESSBOOKSERVER-MEMBER",
    }
)

_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})")
_DATA_URI_RE = re.compile(r"^data:[^,]*;base64,(?P<data>.*)$", re.S)
_TEL_URI_PREFIX = "tel:"
_UUID_URN_PREFIX = "urn:uuid:"

# The width of the name, organisation, title and UID columns. A name past it
# is cut to fit; a UID past it is refused, because a cut UID would match a
# different contact on the next import.
COLUMN_MAX_LENGTH = 255
_CLIPPED_FIELDS = ("display_name", "given_name", "family_name", "organization", "title")

# Order of preference when a property carries several TYPE values: iOS writes
# ``TEL;type=WORK;type=FAX`` and the fax is the part worth keeping.
_PHONE_TYPE_PREFERENCE = ("cell", "fax", "home", "work")
_PHONE_TYPE_ALIASES = {"mobile": "cell", "iphone": "cell"}
_EMAIL_TYPE_PREFERENCE = ("home", "work")
_ADDRESS_TYPE_PREFERENCE = ("home", "work")


class VCardError(ValueError):
    """The text is not a vCard file."""


@dataclass
class ParsedCard:
    kind: str  # "individual" or "group"
    uid: str
    fields: dict
    photo: bytes | None = None
    member_uids: list[str] = field(default_factory=list)


# ---- parsing --------------------------------------------------------------


def parse_vcards(text):
    """Every card of the file, in order. Raises ``VCardError`` on garbage."""
    if not text.strip():
        return []
    cards = []
    current = None
    depth = 0
    try:
        for raw, number in getLogicalLines(StringIO(text), allowQP=True):
            line = textLineToContentLine(raw, number)
            name = line.name.upper()
            if name == "BEGIN":
                if line.value.upper() == "VCARD" and current is None:
                    current = []
                else:
                    depth += 1
            elif name == "END":
                if depth:
                    depth -= 1
                elif current is not None:
                    cards.append(_parse_card(current))
                    current = None
            elif current is not None and not depth:
                current.append(line)
    except (VObjectError, ValueError) as exc:
        raise VCardError(str(exc)) from exc
    if current is not None:
        raise VCardError("A card was never closed.")
    if not cards:
        raise VCardError("No vCard found.")
    return cards


def _param_values(line, name):
    return [str(v) for v in line.params.get(name, [])]


def _raw_value(line):
    """The wire value, quoted-printable undone (vCard 2.1 exports)."""
    encoding = [v.upper() for v in _param_values(line, "ENCODING")]
    if "QUOTED-PRINTABLE" in encoding:
        charset = (_param_values(line, "CHARSET") or ["utf-8"])[0]
        try:
            return quopri.decodestring(line.value.encode("latin-1")).decode(
                charset, errors="replace"
            )
        except LookupError, ValueError:
            return line.value
    return line.value


def _unescape(raw):
    """A single text value, unescaped without the comma split of a list."""
    return re.sub(
        r"\\(.)", lambda m: {"n": "\n", "N": "\n"}.get(m.group(1), m.group(1)), raw
    )


def _is_base64(line):
    encoding = [v.upper() for v in _param_values(line, "ENCODING")]
    return (
        "B" in encoding
        or "BASE64" in encoding
        or "BASE64" in (p.upper() for p in line.singletonparams)
    )


def _pick_type(line, preference, default, aliases=None):
    declared = {
        (aliases or {}).get(v.lower(), v.lower()) for v in _param_values(line, "TYPE")
    }
    for candidate in preference:
        if candidate in declared:
            return candidate
    return default


def _extra_entry(line):
    entry = {"value": line.value}
    params = {name: _param_values(line, name) for name in line.params}
    for singleton in line.singletonparams:
        params.setdefault(singleton.upper(), [])
    if params:
        entry["params"] = params
    if line.group:
        entry["group"] = line.group
    return entry


def normalize_phone(raw):
    """E.164 when the number is internationally unambiguous, the input otherwise."""
    value = raw.strip()
    if value.lower().startswith(_TEL_URI_PREFIX):
        value = value[len(_TEL_URI_PREFIX) :]
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        return value
    if not phonenumbers.is_possible_number(number):
        return value
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def _parse_birthday(raw):
    match = _DATE_RE.match(raw.strip())
    if match is None:
        return None
    try:
        return datetime.date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _field_text(part):
    """One N or ADR component: a list when it held comma-separated values."""
    if isinstance(part, list):
        return ", ".join(p.strip() for p in part if p.strip())
    return part.strip()


def _parse_address(line):
    parts = [_field_text(p) for p in splitFields(_raw_value(line))]
    parts += [""] * (7 - len(parts))
    box, extended, street, city, region, code, country = parts[:7]
    return {
        "street": "\n".join(p for p in (box, extended, street) if p),
        "city": city,
        "region": region,
        "postal_code": code,
        "country": country,
        "type": _pick_type(line, _ADDRESS_TYPE_PREFERENCE, "home"),
    }


def _parse_photo(line):
    """The image bytes, or ``None`` when the property is not inline data."""
    raw = line.value.strip()
    if _is_base64(line):
        data = raw
    else:
        match = _DATA_URI_RE.match(raw)
        if match is None:
            return None
        data = match.group("data")
    try:
        return base64.b64decode("".join(data.split()), validate=False)
    except ValueError:
        return None


def _parse_card(lines):
    fields = {
        "display_name": "",
        "given_name": "",
        "family_name": "",
        "organization": "",
        "title": "",
        "birthday": None,
        "emails": [],
        "phones": [],
        "addresses": [],
        "notes": "",
        "extra_properties": {},
    }
    extra = fields["extra_properties"]
    uid = ""
    kind = "individual"
    photo = None
    member_uids = []
    name_line = None

    for line in lines:
        name = line.name.upper()
        if name == "FN":
            fields["display_name"] = _unescape(_raw_value(line)).strip()
        elif name == "N":
            name_line = line
        elif name == "ORG":
            components = [_field_text(p) for p in splitFields(_raw_value(line))]
            fields["organization"] = ", ".join(c for c in components if c)
        elif name == "TITLE":
            fields["title"] = _unescape(_raw_value(line)).strip()
        elif name == "BDAY":
            birthday = _parse_birthday(_raw_value(line))
            if birthday is None:
                extra.setdefault(name, []).append(_extra_entry(line))
            else:
                fields["birthday"] = birthday
        elif name == "EMAIL":
            value = _unescape(_raw_value(line)).strip()
            if value:
                fields["emails"].append(
                    {
                        "value": value,
                        "type": _pick_type(line, _EMAIL_TYPE_PREFERENCE, "other"),
                    }
                )
        elif name == "TEL":
            value = normalize_phone(_unescape(_raw_value(line)))
            if value:
                fields["phones"].append(
                    {
                        "value": value,
                        "type": _pick_type(
                            line, _PHONE_TYPE_PREFERENCE, "other", _PHONE_TYPE_ALIASES
                        ),
                    }
                )
        elif name == "ADR":
            address = _parse_address(line)
            if any(
                address[key]
                for key in ("street", "city", "region", "postal_code", "country")
            ):
                fields["addresses"].append(address)
        elif name == "NOTE":
            fields["notes"] = _unescape(_raw_value(line))
        elif name == "UID":
            uid = _unescape(_raw_value(line)).strip()
        elif name in ("KIND", "X-ADDRESSBOOKSERVER-KIND"):
            if _raw_value(line).strip().lower() == "group":
                kind = "group"
        elif name in ("MEMBER", "X-ADDRESSBOOKSERVER-MEMBER"):
            member_uids.append(_unescape(_raw_value(line)).strip())
        elif name == "PHOTO":
            # The first image wins; any other PHOTO line is kept for the round trip.
            decoded = _parse_photo(line) if photo is None else None
            if decoded is None:
                extra.setdefault(name, []).append(_extra_entry(line))
            else:
                photo = decoded
        elif name not in _CONSUMED:
            extra.setdefault(name, []).append(_extra_entry(line))

    if name_line is not None:
        _apply_name(name_line, fields)
    if not fields["display_name"]:
        fields["display_name"] = _fallback_display_name(fields, kind)
    for name in _CLIPPED_FIELDS:
        fields[name] = fields[name][:COLUMN_MAX_LENGTH]
    if len(uid) > COLUMN_MAX_LENGTH:
        raise VCardError("A UID is longer than 255 characters.")
    return ParsedCard(
        kind=kind, uid=uid, fields=fields, photo=photo, member_uids=member_uids
    )


def _apply_name(line, fields):
    raw = _raw_value(line)
    parts = [_field_text(p) for p in splitFields(raw)]
    parts += [""] * (5 - len(parts))
    family, given, additional, prefix, suffix = parts[:5]
    fields["given_name"] = given
    fields["family_name"] = family
    if additional or prefix or suffix:
        # The columns hold two of the five parts; the raw value is kept so
        # the export can rebuild the other three around them.
        fields["extra_properties"]["N"] = [{"value": raw}]


def _fallback_display_name(fields, kind):
    full = " ".join(p for p in (fields["given_name"], fields["family_name"]) if p)
    if full:
        return full
    if fields["organization"]:
        return fields["organization"]
    if fields["emails"]:
        return fields["emails"][0]["value"]
    return "Unnamed list" if kind == "group" else "Unnamed"


# ---- export ---------------------------------------------------------------


def person_uid(person):
    return person.import_uid or f"{_UUID_URN_PREFIX}{person.uuid}"


def _add(card, name, value, *, params=None, group=None):
    line = card.add(name)
    line.value = value
    for key, values in (params or {}).items():
        line.params[key] = list(values)
    if group:
        line.group = group
    return line


def _add_raw(card, name, raw, *, params=None, group=None):
    """A line in wire form already: vobject must not escape it again."""
    line = card.add(ContentLine(name.upper(), [], "", group=group or None))
    # Set after the add: adding runs the property's decoder, which would
    # split the value on its commas and mark it for escaping on the way out.
    line.value = raw
    line.encoded = True
    for key, values in (params or {}).items():
        line.params[key] = list(values)
    return line


def _add_typed(card, name, value, entry_type):
    params = {"TYPE": [entry_type]} if entry_type and entry_type != "other" else None
    _add(card, name, value, params=params)


def _name_value(person):
    stored = (person.extra_properties or {}).get("N")
    parts = [_field_text(p) for p in splitFields(stored[0]["value"])] if stored else []
    parts += [""] * (5 - len(parts))
    return vobject.vcard.Name(
        family=person.family_name,
        given=person.given_name,
        additional=parts[2],
        prefix=parts[3],
        suffix=parts[4],
    )


def _address_value(entry):
    return vobject.vcard.Address(
        street=entry.get("street", ""),
        city=entry.get("city", ""),
        region=entry.get("region", ""),
        code=entry.get("postal_code", ""),
        country=entry.get("country", ""),
    )


def person_to_vcard(person, *, photo=None, photo_type="webp"):
    """A vCard 4.0 component for the person; ``photo`` is inlined when given."""
    card = vobject.vCard()
    _add(card, "version", "4.0")
    _add(card, "uid", person_uid(person))
    _add(card, "fn", person.display_name)
    extra = person.extra_properties or {}
    if person.given_name or person.family_name or "N" in extra:
        _add(card, "n", _name_value(person))
    if person.organization:
        _add(card, "org", [person.organization])
    if person.title:
        _add(card, "title", person.title)
    if person.birthday:
        _add(card, "bday", person.birthday.strftime("%Y%m%d"))
    for entry in person.emails or []:
        _add_typed(card, "email", entry["value"], entry.get("type"))
    for entry in person.phones or []:
        _add_typed(card, "tel", entry["value"], entry.get("type"))
    for entry in person.addresses or []:
        _add_typed(card, "adr", _address_value(entry), entry.get("type"))
    if person.notes:
        _add(card, "note", person.notes)
    if photo is not None:
        encoded = base64.b64encode(photo).decode()
        _add_raw(card, "photo", f"data:image/{photo_type};base64,{encoded}")
    for name, entries in extra.items():
        if name == "N":
            continue
        for entry in entries:
            _add_raw(
                card,
                name,
                entry.get("value", ""),
                params=entry.get("params"),
                group=entry.get("group"),
            )
    return card


def person_to_qr_vcard(person, *, include_notes=True):
    """The person as a compact vCard 3.0, serialized.

    3.0 rather than the 4.0 the file export writes: this card is read by
    whatever camera app the other phone ships with, and 3.0 is the version
    every one of them understands. Everything kept only so a re-import round
    trips - the photo, the UID, the properties with no column - is left out,
    because a QR code holds about two kilobytes in total and an avatar alone
    is past that.
    """
    card = vobject.vCard()
    _add(card, "version", "3.0")
    _add(card, "fn", person.display_name)
    extra = person.extra_properties or {}
    if person.given_name or person.family_name or "N" in extra:
        _add(card, "n", _name_value(person))
    if person.organization:
        _add(card, "org", [person.organization])
    if person.title:
        _add(card, "title", person.title)
    if person.birthday:
        _add(card, "bday", person.birthday.isoformat())
    for entry in person.emails or []:
        _add_typed(card, "email", entry["value"], entry.get("type"))
    for entry in person.phones or []:
        _add_typed(card, "tel", entry["value"], entry.get("type"))
    for entry in person.addresses or []:
        _add_typed(card, "adr", _address_value(entry), entry.get("type"))
    for entry in extra.get("URL", []):
        _add_raw(card, "URL", entry.get("value", ""))
    if include_notes and person.notes:
        _add(card, "note", person.notes)
    return card.serialize()


def list_to_vcard(person_list, members):
    card = vobject.vCard()
    _add(card, "version", "4.0")
    _add(card, "kind", "group")
    _add(card, "uid", f"{_UUID_URN_PREFIX}{person_list.uuid}")
    _add(card, "fn", person_list.name)
    for member in members:
        _add(card, "member", person_uid(member))
    return card


def serialize_cards(cards):
    return "".join(card.serialize() for card in cards)

"""Flat address columns for mail storage and search.

The sender is stored directly in the plain ``from_email`` / ``from_name``
columns (``sender_columns`` maps the parsed ``{name, email}`` dict to
them at write time). Recipients keep their structured JSON lists as the
source of truth, and ``derive_recipients_text`` flattens them into the
search-only ``recipients_text`` column so search paths can scan a plain
text column instead of casting JSON per row.
"""

FROM_EMAIL_MAX_LENGTH = 254
FROM_NAME_MAX_LENGTH = 255


def sender_columns(address):
    """Map a parsed ``{name, email}`` dict to the flat sender columns.

    Tolerates malformed input (non-dict, missing keys) and truncates to
    the column limits so Postgres doesn't reject oversized headers.
    """
    sender = address if isinstance(address, dict) else {}
    return {
        "from_email": (sender.get("email") or "").strip()[:FROM_EMAIL_MAX_LENGTH],
        "from_name": (sender.get("name") or "").strip()[:FROM_NAME_MAX_LENGTH],
    }


def _flatten_entry(entry):
    """Render one {name, email} dict as ``Name <email>``, ``email`` or ``Name``."""
    if not isinstance(entry, dict):
        return ""
    name = (entry.get("name") or "").strip()
    email = (entry.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return email or name


def derive_recipients_text(to_addresses, cc_addresses):
    """Flatten the to/cc JSON lists into one searchable text value."""
    parts = []
    for field in (to_addresses, cc_addresses):
        if isinstance(field, list):
            parts.extend(filter(None, (_flatten_entry(entry) for entry in field)))
    return ", ".join(parts)

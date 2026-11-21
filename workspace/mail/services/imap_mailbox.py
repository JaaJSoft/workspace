"""IMAP mailbox names: listing, encoding, display, type detection.

A mailbox name travels in modified UTF-7 (RFC 3501, section 5.1.3):
"Réunions" is ``R&AOk-unions`` on the wire. `MailFolder.name` holds that
wire form, exactly as the server spells it, so a name read from a LIST goes
back unchanged in the next SELECT whatever it contains. Only the two ends
convert, both with IMAPClient's codec: `encode_name` for what the user
types, `_display_name` for what the user reads.
"""

from imapclient import imap_utf7

# Special-use folder attributes (RFC 6154), lowercased
SPECIAL_USE_MAP = {
    "\\sent": "sent",
    "\\drafts": "drafts",
    "\\trash": "trash",
    "\\jstrash": "trash",
    "\\junk": "spam",
    "\\spam": "spam",
    "\\archive": "archive",
    "\\all": "archive",
}

# Fallback name-based detection
NAME_TYPE_MAP = {
    "inbox": "inbox",
    "sent": "sent",
    "sent mail": "sent",
    "sent items": "sent",
    "drafts": "drafts",
    "draft": "drafts",
    "trash": "trash",
    "deleted": "trash",
    "deleted items": "trash",
    "bin": "trash",
    "junk": "spam",
    "spam": "spam",
    "archive": "archive",
    "all mail": "archive",
}


def encode_name(name):
    """The wire form of a mailbox name the user typed."""
    return imap_utf7.encode(name).decode("ascii")


def _detect_folder_type(name, flags):
    """Detect folder type from special-use attributes or name.

    `flags` are the lowercased LIST attributes of the mailbox.
    """
    for attribute, folder_type in SPECIAL_USE_MAP.items():
        if attribute in flags:
            return folder_type

    # Name-based detection
    lower_name = name.lower().split("/")[-1].split(".")[-1]
    if lower_name in NAME_TYPE_MAP:
        return NAME_TYPE_MAP[lower_name]

    # INBOX is always inbox
    if name.upper() == "INBOX":
        return "inbox"

    return "other"


def _display_name(name):
    """Derive a human-readable display name from an IMAP folder name."""
    # Take last segment after / or .
    for sep in ("/", "."):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    try:
        return imap_utf7.decode(name.encode("ascii"))
    except ValueError:
        # Not modified UTF-7 after all (a server sending raw UTF-8, a
        # truncated sequence): the raw value is the best label there is.
        return name


def list_folders(client):
    """Every mailbox of the account, as ``(flags, delimiter, name)``.

    `flags` is the set of the mailbox's LIST attributes, lowercased;
    `name` is its wire form.
    """
    return [
        (
            frozenset(_text(flag).lower() for flag in flags),
            _text(delimiter),
            _text(name),
        )
        for flags, delimiter, name in client.list_folders()
    ]


def _text(value):
    """A LIST field as text: IMAPClient leaves atoms as bytes, and reads a
    mailbox named with digits alone as a number."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)

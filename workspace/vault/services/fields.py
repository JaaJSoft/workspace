"""The closed catalogue of entry field identifiers.

An identifier is not a label: it is the last component of the associated data
an entry field's ciphertext is sealed under. Two identifiers that collapse onto
one string make their ciphertexts interchangeable, and a swap between them
passes AEAD verification - the one attack associated data exists to close.

Deliberately independent of ``tests/reference/ad.py``, which is the oracle the
browser is measured against. Sharing one implementation would make their
agreement circular.
"""

import re

# What an entry type's FIELD_SCHEMA may declare. Anything a user adds is
# prefixed instead.
RESERVED_FIELD_IDS = frozenset({"username", "password", "totp", "uri"})

# Carried by VaultEntry.encrypted_name and encrypted_notes, which live in
# another table and so escape unique(entry, field_id). An EntryField deriving
# either string would let a ciphertext be swapped between the two and still
# verify, so no field id may ever produce them.
ENTRY_COLUMN_FIELD_IDS = frozenset({"name", "notes"})

CUSTOM_PREFIX = "custom:"

# ASCII, and no colon: the colon is the prefix separator, and a non-ASCII label
# would build associated data the reference implementation refuses to encode -
# the browser would seal a value nothing else can open.
_CUSTOM_LABEL = re.compile(r"^[A-Za-z0-9._~-]{1,64}\Z")


def qualify_field_id(field_id: str) -> str:
    """Return the stored identifier, or raise ``ValueError``.

    Identity, never a transformation: ``pin`` and ``custom:pin`` are both legal
    rows under ``unique(entry, field_id)``, so a mapping that turned one into
    the other would let their ciphertexts be swapped. Producing a stored
    identifier from a user's label is :func:`custom_field_id`'s job.
    """
    if field_id in RESERVED_FIELD_IDS:
        return field_id
    if not field_id.startswith(CUSTOM_PREFIX):
        raise ValueError(f"field id is neither reserved nor {CUSTOM_PREFIX}-prefixed")
    label = field_id[len(CUSTOM_PREFIX) :]
    if not _CUSTOM_LABEL.match(label):
        raise ValueError("custom field label is malformed")
    return field_id


def custom_field_id(label: str) -> str:
    """The stored identifier for a field a user just named."""
    return qualify_field_id(f"{CUSTOM_PREFIX}{label}")

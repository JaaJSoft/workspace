"""The info and associated-data catalogue.

These strings ARE the format. Changing one does not break a build, it breaks
the decryption of every value already written with it, and nothing fails until
a user opens the entry. ASCII only, `|` as the separator, RFC 4122 lowercase
UUIDs, no trailing newline.
"""

# System identifiers an entry type may declare. Anything else a user adds is
# mechanically prefixed, which is what keeps a custom field from colliding with
# a system one.
RESERVED_FIELD_IDS = frozenset({"username", "password", "totp", "uri"})

# Carried by VaultEntry.encrypted_name / encrypted_notes, which live in another
# table and so escape unique(entry, field_id). An EntryField deriving the same
# associated data would let a ciphertext be swapped between the two and still
# verify: the database refuses the raw values and this module refuses to
# qualify them.
ENTRY_COLUMN_FIELD_IDS = frozenset({"name", "notes"})

CUSTOM_PREFIX = "custom:"


def _uuid(value: str) -> str:
    return str(value).lower()


def unwrap_info() -> bytes:
    return b"v1|unwrap"


def entry_key_info(entry_uuid: str) -> bytes:
    return f"v1|entry-key|{_uuid(entry_uuid)}".encode("ascii")


def kex_priv_ad(user_uuid: str) -> bytes:
    return f"v1|account-kex-priv|{_uuid(user_uuid)}".encode("ascii")


def sig_priv_ad(user_uuid: str) -> bytes:
    return f"v1|account-sig-priv|{_uuid(user_uuid)}".encode("ascii")


def entry_field_ad(entry_uuid: str, field_name: str) -> bytes:
    return f"v1|entry-field|{_uuid(entry_uuid)}|{field_name}".encode("ascii")


def kex_pub_payload(user_uuid: str, kex_pub_b64: str) -> bytes:
    return f"v1|account-kex-pub|{_uuid(user_uuid)}|{kex_pub_b64}".encode("ascii")


def vault_key_info(vault_uuid: str, recipient_uuid: str) -> bytes:
    return f"v1|vault-key|{_uuid(vault_uuid)}|{_uuid(recipient_uuid)}".encode("ascii")


def qualify_field_id(field_id: str) -> str:
    """Return the field_name that goes into the AD for a stored *field_id*.

    Identity, never a transformation: `x` and `custom:x` are both legal rows
    under unique(entry, field_id), so a mapping that collapsed them onto one AD
    would let their ciphertexts be swapped and still verify. Producing a stored
    identifier from a user's label is the write path's job.
    """
    if field_id in RESERVED_FIELD_IDS:
        return field_id
    if not field_id.startswith(CUSTOM_PREFIX):
        raise ValueError(
            f"field id {field_id!r} is neither reserved nor {CUSTOM_PREFIX}-prefixed"
        )
    label = field_id[len(CUSTOM_PREFIX) :]
    if not label or ":" in label:
        raise ValueError(f"field id {field_id!r} carries a malformed custom label")
    return field_id

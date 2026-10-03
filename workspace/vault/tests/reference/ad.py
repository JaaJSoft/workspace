"""The info and associated-data catalogue.

These strings ARE the format. Changing one does not break a build, it breaks
the decryption of every value already written with it, and nothing fails until
a user opens the entry. ASCII only, `|` as the separator, RFC 4122 lowercase
UUIDs, no trailing newline.

An account is named by the UUID of its AccountIdentity row, never by a user
id: Django's auth.User has an integer primary key, which is enumerable and
reassignable once an account is deleted - an associated data string another
human could one day inherit.

An associated-data builder returns a `Context`, not bytes: which prefix it
gets, and whether the wire header goes in front, is decided by the format of
the ciphertext it opens.
"""

from dataclasses import dataclass

from .suites import entry, stored_hpke_format

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


@dataclass(frozen=True)
class Context:
    """The part of an associated-data string that names a slot."""

    body: str

    def __post_init__(self):
        # Field ids come from users; refusing non-ASCII here keeps the browser
        # and this implementation from building different bytes.
        self.body.encode("ascii")


def associated_data(context, header: bytes) -> bytes:
    """The associated data a ciphertext with this header was sealed under.

    A ``bytes`` value is caller-owned (the archive passes its container
    header): format 2 still puts the wire header in front of it.
    """
    fmt = entry("format", header[0])
    body = (
        (fmt["ad_prefix"] + context.body).encode("ascii")
        if isinstance(context, Context)
        else bytes(context)
    )
    return (header + body) if fmt["header_in_ad"] else body


def unwrap_info() -> bytes:
    return b"v1|unwrap"


def entry_key_info(entry_uuid: str) -> bytes:
    return f"v1|entry-key|{_uuid(entry_uuid)}".encode("ascii")


def kex_priv_ad(account_uuid: str) -> Context:
    return Context(f"account-kex-priv|{_uuid(account_uuid)}")


def sig_priv_ad(account_uuid: str) -> Context:
    return Context(f"account-sig-priv|{_uuid(account_uuid)}")


def entry_field_ad(entry_uuid: str, field_name: str) -> Context:
    return Context(f"entry-field|{_uuid(entry_uuid)}|{field_name}")


def kex_pub_payload(account_uuid: str, kex_pub_b64: str) -> bytes:
    return f"v1|account-kex-pub|{_uuid(account_uuid)}|{kex_pub_b64}".encode("ascii")


def vault_key_info(vault_uuid: str, recipient_uuid: str, hpke_suite: dict) -> bytes:
    """HPKE info for a wrapped vault key.

    Format 2 names the suite inside the info: a stored hpke_suite someone
    edited then fails to open instead of selecting another suite silently.
    """
    base = f"vault-key|{_uuid(vault_uuid)}|{_uuid(recipient_uuid)}"
    fmt_id = stored_hpke_format(hpke_suite)
    if fmt_id == 1:
        return f"v1|{base}".encode("ascii")
    fmt = entry("format", fmt_id)
    descriptor = "-".join(
        f"{hpke_suite[field]:04x}" for field in ("kem_id", "kdf_id", "aead_id")
    )
    return f"{fmt['ad_prefix']}{base}|{descriptor}".encode("ascii")


# Closed, like the entry field catalogue and for the same reason: an open list
# would let a vault field derive an associated data string an entry field can
# also derive, and a ciphertext could then be moved between the two and still
# verify.
VAULT_FIELD_IDS = ("name", "description")


def vault_meta_info(vault_uuid: str) -> bytes:
    return f"v1|vault-meta|{_uuid(vault_uuid)}".encode("ascii")


def vault_field_ad(vault_uuid: str, field: str) -> Context:
    if field not in VAULT_FIELD_IDS:
        raise ValueError(f"{field} is not a vault metadata field")
    return Context(f"vault-field|{_uuid(vault_uuid)}|{field}")


# Closed at one identifier each, for the reason the vault and entry catalogues
# are closed: an open list would let a folder field derive a string a tag or an
# entry field can also derive, and their ciphertexts would be interchangeable.
FOLDER_FIELD_IDS = ("name",)
TAG_FIELD_IDS = ("name",)


def folder_field_ad(folder_uuid: str, field: str) -> Context:
    if field not in FOLDER_FIELD_IDS:
        raise ValueError(f"{field} is not a folder metadata field")
    return Context(f"folder-field|{_uuid(folder_uuid)}|{field}")


def tag_field_ad(tag_uuid: str, field: str) -> Context:
    if field not in TAG_FIELD_IDS:
        raise ValueError(f"{field} is not a tag metadata field")
    return Context(f"tag-field|{_uuid(tag_uuid)}|{field}")


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

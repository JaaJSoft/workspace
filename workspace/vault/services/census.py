"""Which algorithm ids the stored rows carry, read from public bytes only.

Every ciphertext and signature is base64url text; its first four characters
are exactly its first three bytes, which hold the format, the AEAD and - for a
signature or a public key - the one-byte algorithm prefix. Only those four
characters are fetched: counting a vault does not move its ciphertexts.
"""

import base64
import binascii
from collections import Counter

from django.db.models import Count
from django.db.models.functions import Substr

from ..models import (
    AccountIdentity,
    EntryField,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultRole,
    VaultTag,
)
from ..queries import (
    accessible_entries_q,
    accessible_entry_fields_q,
    accessible_folders_q,
    accessible_tags_q,
    user_vault_ids,
    vault_roles,
)
from . import suites

type Mark = tuple[str, int | str]

_HEAD = 4
UNREADABLE_ID = "?"
_UNREADABLE: Mark = ("format", UNREADABLE_ID)


def _head_bytes(head: str) -> bytes | None:
    if len(head) < _HEAD:
        return None
    try:
        return base64.b64decode(
            head[:_HEAD].replace("-", "+").replace("_", "/"), validate=True
        )
    except binascii.Error, ValueError:
        return None


def ciphertext_marks(head: str) -> list[Mark]:
    if not head:
        return []
    raw = _head_bytes(head)
    if raw is None or len(raw) < 2:
        return [_UNREADABLE]
    return [("format", raw[0]), ("aead", raw[1])]


def _prefix_marks(axis: str, head: str) -> list[Mark]:
    if not head:
        return []
    raw = _head_bytes(head.ljust(_HEAD, "A")) if len(head) >= 2 else None
    return [(axis, raw[0])] if raw else [(axis, UNREADABLE_ID)]


def signature_marks(head: str) -> list[Mark]:
    return _prefix_marks("signature", head)


def pubkey_marks(head: str) -> list[Mark]:
    return _prefix_marks("pubkey", head)


def hpke_marks(value) -> list[Mark]:
    fmt = suites.hpke_format(value)
    return [("hpke", UNREADABLE_ID if fmt is None else fmt)]


def is_stale(marks) -> bool:
    return any(
        suites.state(axis, identifier) == "superseded" for axis, identifier in marks
    )


def _heads(queryset, *columns):
    return queryset.annotate(
        **{f"h_{column}": Substr(column, 1, _HEAD) for column in columns}
    )


def stale_rows(user) -> list[dict]:
    vault_ids = list(user_vault_ids(user))
    roles = vault_roles(user, vault_ids)
    listing = {}

    def slot(vault_id):
        return listing.setdefault(
            vault_id,
            {
                "uuid": str(vault_id),
                "metadata": False,
                "wrap": False,
                "folders": [],
                "tags": [],
                "entries": [],
            },
        )

    for row in _heads(
        Vault.objects.filter(uuid__in=vault_ids),
        "encrypted_name",
        "encrypted_description",
        "metadata_sig",
    ).values("uuid", "h_encrypted_name", "h_encrypted_description", "h_metadata_sig"):
        marks = (
            ciphertext_marks(row["h_encrypted_name"])
            + ciphertext_marks(row["h_encrypted_description"])
            + signature_marks(row["h_metadata_sig"])
        )
        if roles.get(row["uuid"]) == VaultRole.OWNER and is_stale(marks):
            slot(row["uuid"])["metadata"] = True

    for wrap in VaultKeyWrap.objects.filter(
        recipient=user, vault_id__in=vault_ids
    ).values("vault_id", "hpke_suite"):
        if is_stale(hpke_marks(wrap["hpke_suite"])):
            slot(wrap["vault_id"])["wrap"] = True

    for model, reach, key in (
        (VaultFolder, accessible_folders_q, "folders"),
        (VaultTag, accessible_tags_q, "tags"),
    ):
        for row in _heads(
            model.objects.filter(reach(user)),
            "encrypted_name",
            "metadata_sig",
        ).values("uuid", "vault_id", "h_encrypted_name", "h_metadata_sig"):
            if is_stale(
                ciphertext_marks(row["h_encrypted_name"])
                + signature_marks(row["h_metadata_sig"])
            ):
                slot(row["vault_id"])[key].append(str(row["uuid"]))

    stale_entries = {}
    for row in _heads(
        VaultEntry.objects.filter(accessible_entries_q(user)),
        "encrypted_name",
        "encrypted_notes",
        "metadata_sig",
    ).values(
        "uuid", "vault_id", "h_encrypted_name", "h_encrypted_notes", "h_metadata_sig"
    ):
        marks = (
            ciphertext_marks(row["h_encrypted_name"])
            + ciphertext_marks(row["h_encrypted_notes"])
            + signature_marks(row["h_metadata_sig"])
        )
        if is_stale(marks):
            stale_entries[row["uuid"]] = row["vault_id"]
    for row in _heads(
        EntryField.objects.filter(accessible_entry_fields_q(user)),
        "encrypted_value",
    ).values("entry_id", "entry__vault_id", "h_encrypted_value"):
        if row["entry_id"] not in stale_entries and is_stale(
            ciphertext_marks(row["h_encrypted_value"])
        ):
            stale_entries[row["entry_id"]] = row["entry__vault_id"]
    for entry_id, vault_id in stale_entries.items():
        slot(vault_id)["entries"].append(str(entry_id))

    for item in listing.values():
        for key in ("folders", "tags", "entries"):
            item[key].sort()
    return sorted(listing.values(), key=lambda item: item["uuid"])


_PREFIX_HEAD = 2


def _grouped(queryset, column, length, marks):
    """Counts per mark of one column, grouped on its head in SQL: a handful of
    rows back whatever the table holds. A ciphertext head is three header
    bytes, a signature or key head only the prefix byte, so neither carries
    enough of the random body to spread the groups."""
    counts = Counter()
    for head, rows in (
        queryset.annotate(h=Substr(column, 1, length))
        .values("h")
        .annotate(rows=Count("pk"))
        .values_list("h", "rows")
    ):
        # A NULL column - an empty previous envelope - counts nothing.
        for mark in marks(head or ""):
            counts[mark] += rows
    return counts


def census(using: str = "default") -> Counter:
    columns = [
        (Vault, "encrypted_name", ciphertext_marks),
        (Vault, "encrypted_description", ciphertext_marks),
        (Vault, "metadata_sig", signature_marks),
        (VaultFolder, "encrypted_name", ciphertext_marks),
        (VaultFolder, "metadata_sig", signature_marks),
        (VaultTag, "encrypted_name", ciphertext_marks),
        (VaultTag, "metadata_sig", signature_marks),
        (VaultEntry, "encrypted_name", ciphertext_marks),
        (VaultEntry, "encrypted_notes", ciphertext_marks),
        (VaultEntry, "metadata_sig", signature_marks),
        (EntryField, "encrypted_value", ciphertext_marks),
        (AccountIdentity, "wrapped_kex_priv", ciphertext_marks),
        (AccountIdentity, "wrapped_sig_priv", ciphertext_marks),
        (AccountIdentity, "previous_wrapped_kex_priv", ciphertext_marks),
        (AccountIdentity, "previous_wrapped_sig_priv", ciphertext_marks),
        (AccountIdentity, "kex_public", pubkey_marks),
        (AccountIdentity, "sig_public", pubkey_marks),
        (AccountIdentity, "sig_over_kex_pub", signature_marks),
    ]
    counts = Counter()
    for model, column, marks in columns:
        length = _HEAD if marks is ciphertext_marks else _PREFIX_HEAD
        counts += _grouped(model.objects.using(using), column, length, marks)
    for suite, rows in (
        VaultKeyWrap.objects.using(using)
        .values("hpke_suite")
        .annotate(rows=Count("pk"))
        .values_list("hpke_suite", "rows")
    ):
        for mark in hpke_marks(suite):
            counts[mark] += rows
    for column in ("kdf_algo", "previous_kdf_algo"):
        for algo, rows in (
            AccountIdentity.objects.using(using)
            .exclude(**{f"{column}__isnull": True})
            .values(column)
            .annotate(rows=Count("pk"))
            .values_list(column, "rows")
        ):
            counts[("kdf", algo)] += rows
    return counts

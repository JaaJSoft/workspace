"""Which algorithm ids the stored rows carry, read from public bytes only.

Every ciphertext and signature is base64url text; its first four characters
are exactly its first three bytes, which hold the format, the AEAD and - for a
signature or a public key - the one-byte algorithm prefix. Only those four
characters are fetched: counting a vault does not move its ciphertexts.
"""

import base64
import binascii
from collections import Counter

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
from ..queries import user_vault_ids, vault_roles
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
    return [(axis, raw[0])] if raw else [(axis, "?")]


def signature_marks(head: str) -> list[Mark]:
    return _prefix_marks("signature", head)


def pubkey_marks(head: str) -> list[Mark]:
    return _prefix_marks("pubkey", head)


def hpke_marks(value) -> list[Mark]:
    fmt = suites.hpke_format(value)
    return [("hpke", "?" if fmt is None else fmt)]


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

    for model, key in ((VaultFolder, "folders"), (VaultTag, "tags")):
        for row in _heads(
            model.objects.filter(vault_id__in=vault_ids),
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
        VaultEntry.objects.filter(vault_id__in=vault_ids),
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
        EntryField.objects.filter(entry__vault_id__in=vault_ids), "encrypted_value"
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


def census(using: str = "default") -> Counter:
    counts = Counter()

    for row in (
        _heads(
            Vault.objects.using(using),
            "encrypted_name",
            "encrypted_description",
            "metadata_sig",
        )
        .values_list("h_encrypted_name", "h_encrypted_description", "h_metadata_sig")
        .iterator()
    ):
        counts.update(
            ciphertext_marks(row[0])
            + ciphertext_marks(row[1])
            + signature_marks(row[2])
        )
    for suite in (
        VaultKeyWrap.objects.using(using)
        .values_list("hpke_suite", flat=True)
        .iterator()
    ):
        counts.update(hpke_marks(suite))
    for model in (VaultFolder, VaultTag):
        for name, sig in (
            _heads(model.objects.using(using), "encrypted_name", "metadata_sig")
            .values_list("h_encrypted_name", "h_metadata_sig")
            .iterator()
        ):
            counts.update(ciphertext_marks(name) + signature_marks(sig))
    for name, notes, sig in (
        _heads(
            VaultEntry.objects.using(using),
            "encrypted_name",
            "encrypted_notes",
            "metadata_sig",
        )
        .values_list("h_encrypted_name", "h_encrypted_notes", "h_metadata_sig")
        .iterator()
    ):
        counts.update(
            ciphertext_marks(name) + ciphertext_marks(notes) + signature_marks(sig)
        )
    for (value,) in (
        _heads(EntryField.objects.using(using), "encrypted_value")
        .values_list("h_encrypted_value")
        .iterator()
    ):
        counts.update(ciphertext_marks(value))
    for row in (
        _heads(
            AccountIdentity.objects.using(using),
            "wrapped_kex_priv",
            "wrapped_sig_priv",
            "kex_public",
            "sig_public",
            "sig_over_kex_pub",
        )
        .values_list(
            "h_wrapped_kex_priv",
            "h_wrapped_sig_priv",
            "h_kex_public",
            "h_sig_public",
            "h_sig_over_kex_pub",
            "kdf_algo",
        )
        .iterator()
    ):
        counts.update(
            ciphertext_marks(row[0])
            + ciphertext_marks(row[1])
            + pubkey_marks(row[2])
            + pubkey_marks(row[3])
            + signature_marks(row[4])
            + [("kdf", row[5])]
        )
    return counts

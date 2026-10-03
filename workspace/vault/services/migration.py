"""Applying a batch of migration rewrites, all or nothing.

Three phases, in this order, so a row out of reach is never told apart from a
missing one by a 409: resolve every row (404), check every precondition (409),
then verify and write. Every signed payload is rebuilt from the plaintext
columns already stored - the body carries ciphertexts and a signature only.
"""

from django.db import transaction

from ..models import (
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
    get_vault_role,
    visible_folders,
    visible_tags,
)
from . import suites
from .attestation import AttestationError, decode_base64url
from .entries import entry_signature_payload
from .metadata import (
    folder_metadata_payload,
    tag_metadata_payload,
    vault_metadata_payload,
    verify_record,
)


class RowOutOfReach(Exception):
    pass


class Conflict(Exception):
    def __init__(self, kind, uuid):
        super().__init__(kind)
        self.kind, self.uuid = kind, uuid


class Refused(Exception):
    """``detail`` is always a literal chosen at the raise site."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def _resolve(user, vault, item):
    kind = item["kind"]
    if kind == "metadata":
        if get_vault_role(user, vault) != VaultRole.OWNER:
            raise RowOutOfReach
        return vault
    if kind == "wrap":
        wrap = VaultKeyWrap.objects.filter(vault=vault, recipient=user).first()
        if wrap is None:
            raise RowOutOfReach
        return wrap
    if kind == "folder":
        row = visible_folders(user, vault).filter(uuid=item["uuid"]).first()
    elif kind == "tag":
        row = visible_tags(user, vault).filter(uuid=item["uuid"]).first()
    else:
        row = (
            VaultEntry.objects.filter(
                accessible_entries_q(user), vault=vault, uuid=item["uuid"]
            )
            .prefetch_related("fields", "tags")
            .first()
        )
    if row is None:
        raise RowOutOfReach
    return row


def _expected(row, item):
    if item["kind"] == "wrap":
        return row.wrapped_key == item["wrapped_key_expected"]
    return row.metadata_sig == item["expected_sig"]


def _current(value, key_version, *, may_be_empty, was_empty):
    """A rewrite keeps empty values empty and non-empty ones non-empty, and
    every non-empty one is under the current suite."""
    if not value:
        if not (may_be_empty and was_empty):
            raise Refused("A ciphertext was emptied or filled.")
        return
    if may_be_empty and was_empty:
        raise Refused("A ciphertext was emptied or filled.")
    try:
        suites.check_current_ciphertext(decode_base64url(value), key_version)
    except suites.UnsupportedCiphertext, AttestationError:
        raise Refused("A ciphertext is not under the current suite.") from None


def _verify(payload, identity, signature):
    try:
        verify_record(payload, identity.sig_public, signature)
    except AttestationError:
        raise Refused("A signature does not verify.") from None


def _check(identity, vault, row, item):
    kind = item["kind"]
    if kind == "wrap":
        if item["hpke_suite"] != suites.current_hpke_suite():
            raise Refused("The key wrap is not under the current suite.")
        return
    if kind == "metadata":
        _current(
            item["encrypted_name"],
            vault.key_version,
            may_be_empty=False,
            was_empty=False,
        )
        _current(
            item["encrypted_description"],
            vault.key_version,
            may_be_empty=True,
            was_empty=not vault.encrypted_description,
        )
        _verify(
            vault_metadata_payload(
                vault_uuid=vault.uuid,
                owner_account_uuid=identity.uuid,
                encrypted_name=item["encrypted_name"],
                encrypted_description=item["encrypted_description"],
                icon=vault.icon,
                color=vault.color,
                key_version=vault.key_version,
                is_favorite=vault.is_favorite,
            ),
            identity,
            item["metadata_sig"],
        )
        return
    if kind in ("folder", "tag"):
        _current(
            item["encrypted_name"],
            vault.key_version,
            may_be_empty=False,
            was_empty=False,
        )
        if kind == "folder":
            payload = folder_metadata_payload(
                folder_uuid=row.uuid,
                vault_uuid=vault.uuid,
                signer_account_uuid=identity.uuid,
                parent_uuid=row.parent_id,
                position=row.position,
                encrypted_name=item["encrypted_name"],
            )
        else:
            payload = tag_metadata_payload(
                tag_uuid=row.uuid,
                vault_uuid=vault.uuid,
                signer_account_uuid=identity.uuid,
                encrypted_name=item["encrypted_name"],
                color=row.color,
            )
        _verify(payload, identity, item["metadata_sig"])
        return
    stored_fields = {field.field_id for field in row.fields.all()}
    if set(item["fields"]) != stored_fields:
        raise Refused("A migration cannot add or remove a field.")
    _current(
        item["encrypted_name"], row.key_version, may_be_empty=False, was_empty=False
    )
    _current(
        item["encrypted_notes"],
        row.key_version,
        may_be_empty=True,
        was_empty=not row.encrypted_notes,
    )
    for value in item["fields"].values():
        _current(value, row.key_version, may_be_empty=False, was_empty=False)
    probe = VaultEntry(
        **{f.attname: getattr(row, f.attname) for f in VaultEntry._meta.concrete_fields}
    )
    probe.encrypted_name = item["encrypted_name"]
    probe.encrypted_notes = item["encrypted_notes"]
    _verify(
        entry_signature_payload(
            probe,
            signer_account_uuid=identity.uuid,
            tag_uuids=[tag.uuid for tag in row.tags.all()],
            fields=item["fields"],
        ),
        identity,
        item["metadata_sig"],
    )


def _one(updated, item):
    if updated != 1:
        raise Conflict(item["kind"], item.get("uuid"))


def _write(user, vault, row, item):
    kind = item["kind"]
    if kind == "wrap":
        _one(
            VaultKeyWrap.objects.filter(
                pk=row.pk, recipient=user, wrapped_key=item["wrapped_key_expected"]
            ).update(wrapped_key=item["wrapped_key"], hpke_suite=item["hpke_suite"]),
            item,
        )
    elif kind == "metadata":
        _one(
            Vault.objects.filter(pk=vault.pk, metadata_sig=item["expected_sig"]).update(
                encrypted_name=item["encrypted_name"],
                encrypted_description=item["encrypted_description"],
                metadata_sig=item["metadata_sig"],
            ),
            item,
        )
    elif kind in ("folder", "tag"):
        model = VaultFolder if kind == "folder" else VaultTag
        _one(
            model.objects.filter(pk=row.pk, metadata_sig=item["expected_sig"]).update(
                encrypted_name=item["encrypted_name"],
                metadata_sig=item["metadata_sig"],
            ),
            item,
        )
    else:
        # The entry row first: under PostgreSQL its lock serialises this with a
        # concurrent PUT before any field moves.
        _one(
            VaultEntry.objects.filter(
                pk=row.pk, metadata_sig=item["expected_sig"]
            ).update(
                encrypted_name=item["encrypted_name"],
                encrypted_notes=item["encrypted_notes"],
                metadata_sig=item["metadata_sig"],
            ),
            item,
        )
        for field_id, value in item["fields"].items():
            _one(
                EntryField.objects.filter(entry_id=row.pk, field_id=field_id).update(
                    encrypted_value=value
                ),
                item,
            )


@transaction.atomic
def apply_batch(user, identity, vault, items):
    rows = [_resolve(user, vault, item) for item in items]
    for row, item in zip(rows, items, strict=True):
        if not _expected(row, item):
            raise Conflict(item["kind"], item.get("uuid"))
    for row, item in zip(rows, items, strict=True):
        _check(identity, vault, row, item)
    for row, item in zip(rows, items, strict=True):
        _write(user, vault, row, item)

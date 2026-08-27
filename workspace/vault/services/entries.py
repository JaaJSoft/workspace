"""Writing an entry: one row, its fields, its tags, one signature.

All three in one transaction, because the signature covers all three. A write
split across requests would leave a stored signature matching nothing, and the
client that next opened the entry would read a legitimate half-write as
tampering.
"""

from django.db import transaction

from ..models import EntryField, VaultEntry
from ..queries import visible_folders, visible_tags
from .metadata import entry_metadata_payload


class EntryWriteError(Exception):
    """A folder or a tag the caller named is not in this vault.

    One exception for both because the answer is the same 400: naming a row
    from another vault and naming one that does not exist are the client's
    error either way, and telling them apart would say whether a UUID exists
    somewhere the caller cannot see.
    """


def entry_signature_payload(entry, *, signer_account_uuid, tag_uuids, fields):
    """The payload the signature covers, built from the row about to be
    written - never from what the request said it was writing."""
    return entry_metadata_payload(
        entry_uuid=entry.uuid,
        vault_uuid=entry.vault_id,
        signer_account_uuid=signer_account_uuid,
        entry_type=entry.type,
        folder_uuid=entry.folder_id,
        encrypted_name=entry.encrypted_name,
        encrypted_notes=entry.encrypted_notes,
        key_version=entry.key_version,
        entry_version=entry.entry_version,
        is_favorite=entry.is_favorite,
        tag_uuids=tag_uuids,
        fields=fields,
    )


def resolve_folder(user, vault, folder_uuid):
    """The folder, or None when none was named. Raises on anything else.

    ``visible_folders`` is scoped to the vault, so a folder from elsewhere and
    a folder that does not exist both come back empty.
    """
    if folder_uuid is None:
        return None
    folder = visible_folders(user, vault).filter(uuid=folder_uuid).first()
    if folder is None:
        raise EntryWriteError("The folder does not exist in this vault.")
    return folder


def resolve_tags(user, vault, tag_uuids):
    """The tags, in the order asked for. Raises unless every one is in *vault*.

    This is the check ``VaultEntry.clean()`` cannot make: Django validates a
    many-to-many only once the row exists, so a tag from another vault would
    otherwise be attached after the entry was already written.
    """
    wanted = list(dict.fromkeys(str(value) for value in tag_uuids))
    if not wanted:
        return []
    found = {
        str(tag.uuid): tag for tag in visible_tags(user, vault).filter(uuid__in=wanted)
    }
    missing = [value for value in wanted if value not in found]
    if missing:
        raise EntryWriteError("A tag does not exist in this vault.")
    return [found[value] for value in wanted]


@transaction.atomic
def write_entry(entry, *, tags, fields):
    """Store *entry*, its tag set and its complete field set.

    The field set is replaced wholesale rather than diffed: the signature
    covers the whole set, so a diff would be an optimisation of the one
    operation whose atomicity is the point.
    """
    entry.full_clean(exclude=["uuid"])
    entry.save()
    entry.tags.set(tags)
    entry.fields.all().delete()
    EntryField.objects.bulk_create(
        EntryField(entry=entry, field_id=field_id, encrypted_value=value)
        for field_id, value in fields.items()
    )
    return entry


def entry_queryset():
    """Entries with everything a listing renders, in a fixed number of queries."""
    return VaultEntry.objects.select_related("folder").prefetch_related(
        "fields", "tags"
    )

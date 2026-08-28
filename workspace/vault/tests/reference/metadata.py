"""The signed metadata payload of a vault, in the reference implementation.

The key set is frozen: it is what the signature covers, and adding or removing
one invalidates every signature already written. Timestamps are deliberately
absent - the server writes them, so a client re-verifying a vault it did not
just create could not reproduce them, and a legitimate updated_at bump would
read as tampering.
"""

VAULT_METADATA_TYPE = "vault-metadata"


def vault_metadata_payload(
    *,
    vault_uuid: str,
    owner_account_uuid: str,
    encrypted_name: str,
    encrypted_description: str,
    icon: str,
    color: str,
    key_version: int,
    is_favorite: bool,
) -> dict:
    return {
        "v": 1,
        "type": VAULT_METADATA_TYPE,
        "vault_uuid": str(vault_uuid).lower(),
        "owner_account_uuid": str(owner_account_uuid).lower(),
        "encrypted_name": encrypted_name,
        "encrypted_description": encrypted_description,
        "icon": icon,
        "color": color,
        "key_version": key_version,
        "is_favorite": is_favorite,
    }


ENTRY_METADATA_TYPE = "entry-metadata"
FOLDER_METADATA_TYPE = "folder-metadata"
TAG_METADATA_TYPE = "tag-metadata"


def entry_metadata_payload(
    *,
    entry_uuid: str,
    vault_uuid: str,
    signer_account_uuid: str,
    entry_type: str,
    folder_uuid: str | None,
    encrypted_name: str,
    encrypted_notes: str,
    key_version: int,
    entry_version: int,
    is_favorite: bool,
    tag_uuids,
    fields,
) -> dict:
    """The exact key set an entry's signature covers.

    ``tags`` and ``fields`` are in it because associated data cannot see a
    field that was deleted, a ciphertext replayed into its own slot, or a tag
    detached: it only stops one from moving somewhere it does not belong. Both
    are sorted, because a signature over the order rows happened to come back
    in would verify differently on the next read.
    """
    return {
        "v": 1,
        "type": ENTRY_METADATA_TYPE,
        "entry_uuid": str(entry_uuid).lower(),
        "vault_uuid": str(vault_uuid).lower(),
        "signer_account_uuid": str(signer_account_uuid).lower(),
        "entry_type": entry_type,
        "folder_uuid": str(folder_uuid).lower() if folder_uuid else None,
        "encrypted_name": encrypted_name,
        "encrypted_notes": encrypted_notes,
        "key_version": key_version,
        "entry_version": entry_version,
        "is_favorite": is_favorite,
        "tags": sorted(str(value).lower() for value in tag_uuids),
        "fields": [[key, fields[key]] for key in sorted(fields)],
    }


def folder_metadata_payload(
    *,
    folder_uuid: str,
    vault_uuid: str,
    signer_account_uuid: str,
    parent_uuid: str | None,
    position: int,
    encrypted_name: str,
) -> dict:
    """``parent_uuid`` and ``position`` are the reason this payload exists:
    they are plaintext, and nothing else covers them."""
    return {
        "v": 1,
        "type": FOLDER_METADATA_TYPE,
        "folder_uuid": str(folder_uuid).lower(),
        "vault_uuid": str(vault_uuid).lower(),
        "signer_account_uuid": str(signer_account_uuid).lower(),
        "parent_uuid": str(parent_uuid).lower() if parent_uuid else None,
        "position": position,
        "encrypted_name": encrypted_name,
    }


def tag_metadata_payload(
    *,
    tag_uuid: str,
    vault_uuid: str,
    signer_account_uuid: str,
    encrypted_name: str,
    color: str,
) -> dict:
    return {
        "v": 1,
        "type": TAG_METADATA_TYPE,
        "tag_uuid": str(tag_uuid).lower(),
        "vault_uuid": str(vault_uuid).lower(),
        "signer_account_uuid": str(signer_account_uuid).lower(),
        "encrypted_name": encrypted_name,
        "color": color,
    }

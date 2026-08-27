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

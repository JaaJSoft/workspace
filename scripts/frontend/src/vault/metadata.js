// The signed metadata payload of a vault. The key set is frozen: it is what
// the signature covers, and adding or removing one invalidates every signature
// already written. No timestamp is in it - the server writes created_at and
// updated_at, so a client re-verifying a vault it did not just create could
// not reproduce them, and a legitimate update would read as tampering.
export const VAULT_METADATA_TYPE = 'vault-metadata';

export function vaultMetadataPayload({
  vault_uuid, owner_account_uuid, encrypted_name, encrypted_description,
  icon, color, key_version, is_favorite,
}) {
  return {
    v: 1,
    type: VAULT_METADATA_TYPE,
    vault_uuid: String(vault_uuid).toLowerCase(),
    owner_account_uuid: String(owner_account_uuid).toLowerCase(),
    encrypted_name,
    encrypted_description,
    icon,
    color,
    key_version,
    is_favorite,
  };
}

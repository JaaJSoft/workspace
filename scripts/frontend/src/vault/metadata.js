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

export const ENTRY_METADATA_TYPE = 'entry-metadata';
export const FOLDER_METADATA_TYPE = 'folder-metadata';
export const TAG_METADATA_TYPE = 'tag-metadata';

const lower = (value) => String(value).toLowerCase();

// tags and fields are in the entry payload because associated data cannot see
// a field that was deleted, a ciphertext replayed into its own slot, or a tag
// detached: it only stops one from moving somewhere it does not belong.
//
// Both are sorted with the default comparator on purpose: UUIDs and field ids
// are ASCII, so UTF-16 code-unit order and the reference's codepoint order are
// the same order. A non-ASCII field id would break that agreement, which is
// why ad.js refuses one before it can reach here.
export function entryMetadataPayload({
  entry_uuid, vault_uuid, signer_account_uuid, entry_type, folder_uuid,
  encrypted_name, encrypted_notes, key_version, entry_version, is_favorite,
  tag_uuids, fields,
}) {
  return {
    v: 1,
    type: ENTRY_METADATA_TYPE,
    entry_uuid: lower(entry_uuid),
    vault_uuid: lower(vault_uuid),
    signer_account_uuid: lower(signer_account_uuid),
    entry_type,
    folder_uuid: folder_uuid ? lower(folder_uuid) : null,
    encrypted_name,
    encrypted_notes,
    key_version,
    entry_version,
    is_favorite,
    tags: [...tag_uuids].map(lower).sort(),
    fields: Object.keys(fields).sort().map((key) => [key, fields[key]]),
  };
}

// parent_uuid and position are the reason this payload exists: they are
// plaintext, and nothing else covers them.
export function folderMetadataPayload({
  folder_uuid, vault_uuid, signer_account_uuid, parent_uuid, position, encrypted_name,
}) {
  return {
    v: 1,
    type: FOLDER_METADATA_TYPE,
    folder_uuid: lower(folder_uuid),
    vault_uuid: lower(vault_uuid),
    signer_account_uuid: lower(signer_account_uuid),
    parent_uuid: parent_uuid ? lower(parent_uuid) : null,
    position,
    encrypted_name,
  };
}

export function tagMetadataPayload({
  tag_uuid, vault_uuid, signer_account_uuid, encrypted_name, color,
}) {
  return {
    v: 1,
    type: TAG_METADATA_TYPE,
    tag_uuid: lower(tag_uuid),
    vault_uuid: lower(vault_uuid),
    signer_account_uuid: lower(signer_account_uuid),
    encrypted_name,
    color,
  };
}

// Writing an entry: every field sealed, the whole record signed.
//
// There is no partial write of a signed record. The signature covers the
// name, the notes, the folder, the tags and the complete field map, so a
// change to any one of them re-seals and re-signs all of them - which is why
// the endpoint is a PUT and not a PATCH, and why the server refuses to help:
// it would have to forge the account's signature.
//
// A field's key is derived per entry, so an empty value is a *removed*
// field rather than an empty ciphertext. Sealing "" would leave a row
// claiming to carry a password and an action endpoint offering to copy one.
//
// A field the form does not edit is carried as its stored ciphertext rather
// than re-sealed: the AD binds it to this entry and this slot, so it cannot be
// moved, and re-sealing would need a plaintext this page has no reason to open.
window.buildEntryWriteRequest = async function buildEntryWriteRequest(
  session,
  vault,
  draft,
) {
  const V = window.vaultCrypto;
  const keyVersion = draft.keyVersion || vault.key_version || 1;
  const key = await session.openEntryKey(vault.uuid, vault.wrapped_key, draft.uuid);
  const seal = async (text, fieldId) =>
    V.toBase64Url(
      await V.seal(key, new TextEncoder().encode(text), V.AD.entryFieldAd(draft.uuid, fieldId), {
        keyVersion: keyVersion,
        kdfId: V.KDF_HKDF_SHA256,
      }),
    );

  const fields = {};
  // Ciphertexts the form never opened, carried through rather than re-sealed -
  // the same treatment encrypted_notes already gets, for the same reason: the
  // record is signed whole, so a field absent from the payload is a field
  // deleted.
  //
  // Their key version has to match the one being written. `open` takes the key
  // it is handed and ignores the version in the header, so a ciphertext sealed
  // under an older vault key would be stored inside a record claiming the new
  // one and would never open again - silently. Rotation is therefore a full
  // re-seal, and this refuses to write the record that would prove otherwise.
  const carried = draft.carriedFields || {};
  const carriedIds = Object.keys(carried);
  if (carriedIds.length && (draft.keyVersion || vault.key_version) !== vault.key_version) {
    throw new Error(
      `cannot carry fields sealed under key version ${draft.keyVersion} into ${keyVersion}`,
    );
  }
  for (const fieldId of carriedIds.sort()) {
    fields[fieldId] = carried[fieldId];
  }
  for (const fieldId of Object.keys(draft.values || {}).sort()) {
    const value = draft.values[fieldId];
    if (value === '' || value === null || value === undefined) {
      delete fields[fieldId];
      continue;
    }
    fields[fieldId] = await seal(String(value), fieldId);
  }

  const encryptedName = await seal(draft.name, 'name');
  // Notes travel under the entry's own key like any other field, and an empty
  // one stays an empty string: unlike a field it is a column, so there is no
  // "absent" for it to be.
  //
  // A draft that carries no plaintext notes is not a draft that empties them:
  // no form here edits notes, so an edit hands back the ciphertext it was
  // given and the record keeps what it held. Re-sealing would need the
  // plaintext, and opening notes to write them back is the one thing this
  // page refuses to do.
  const encryptedNotes = draft.notes
    ? await seal(draft.notes, 'notes')
    : draft.encryptedNotes || '';
  const tagUuids = [...(draft.tags || [])];

  const payload = V.entryMetadataPayload({
    entry_uuid: draft.uuid,
    vault_uuid: vault.uuid,
    // The entry payload names its signer, not the vault's owner: in v2 a
    // member signs an entry they do not own.
    signer_account_uuid: session.accountUuid(),
    entry_type: draft.type,
    folder_uuid: draft.folder || null,
    encrypted_name: encryptedName,
    encrypted_notes: encryptedNotes,
    key_version: keyVersion,
    entry_version: draft.entryVersion || 1,
    is_favorite: !!draft.favorite,
    tag_uuids: tagUuids,
    fields: fields,
  });

  return {
    uuid: draft.uuid,
    vault: vault.uuid,
    type: draft.type,
    folder: draft.folder || null,
    tags: tagUuids,
    is_favorite: !!draft.favorite,
    encrypted_name: encryptedName,
    encrypted_notes: encryptedNotes,
    fields: fields,
    metadata_sig: await session.sign(payload),
  };
};

// Re-signing a stored row without opening any of it.
//
// The signed payload covers the *ciphertexts*, not the plaintexts, so moving
// an entry between folders or taking a tag off it needs the signing key and
// nothing else. That is what lets a tag be dropped from fifty entries without
// fifty passwords passing through this page.
//
// `changes` may name `folder`, `tags` and `is_favorite` - the three fields of
// the signed payload that are not ciphertext. Anything else would need the
// value re-sealed, which is buildEntryWriteRequest's job.
window.buildEntryResignRequest = async function buildEntryResignRequest(
  session,
  vault,
  row,
  changes,
) {
  const V = window.vaultCrypto;
  const next = Object.assign(
    {
      folder: row.folder,
      tags: [...(row.tags || [])],
      is_favorite: !!row.is_favorite,
    },
    changes || {},
  );
  const fields = {};
  (row.entry_fields || []).forEach(function (field) {
    fields[field.field_id] = field.encrypted_value;
  });

  const payload = V.entryMetadataPayload({
    entry_uuid: row.uuid,
    vault_uuid: vault.uuid,
    signer_account_uuid: session.accountUuid(),
    entry_type: row.type,
    folder_uuid: next.folder || null,
    encrypted_name: row.encrypted_name,
    encrypted_notes: row.encrypted_notes || '',
    key_version: row.key_version || 1,
    entry_version: row.entry_version || 1,
    is_favorite: next.is_favorite,
    tag_uuids: next.tags,
    fields: fields,
  });

  return {
    uuid: row.uuid,
    vault: vault.uuid,
    type: row.type,
    folder: next.folder || null,
    tags: next.tags,
    is_favorite: next.is_favorite,
    encrypted_name: row.encrypted_name,
    encrypted_notes: row.encrypted_notes || '',
    fields: fields,
    metadata_sig: await session.sign(payload),
  };
};

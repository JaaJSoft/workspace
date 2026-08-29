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
  for (const fieldId of Object.keys(draft.values || {}).sort()) {
    const value = draft.values[fieldId];
    if (value === '' || value === null || value === undefined) continue;
    fields[fieldId] = await seal(String(value), fieldId);
  }

  const encryptedName = await seal(draft.name, 'name');
  // Notes travel under the entry's own key like any other field, and an empty
  // one stays an empty string: unlike a field it is a column, so there is no
  // "absent" for it to be.
  const encryptedNotes = draft.notes ? await seal(draft.notes, 'notes') : '';
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

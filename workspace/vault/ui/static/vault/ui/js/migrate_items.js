// Rewriting a verified row under the current suite.
//
// Only the ciphertexts still under a superseded format or AEAD are re-sealed,
// and they go from bytes to bytes: the plaintext is never decoded to a string,
// which nothing could wipe afterwards, and its buffer is zeroed as soon as it
// is sealed again. Everything that is not ciphertext comes from the row as it
// was verified - the server rebuilds the signed payload from its own columns
// and refuses any other change.
//
// The key version is the row's (an entry) or the vault's (its metadata, its
// folders and tags, which carry none), as the signed payload states it. A
// format-1 header is authenticated by nothing, so its own key version is never
// read here.
window.vaultReseal = async function vaultReseal(V, key, b64, context, keyVersion) {
  const raw = V.fromBase64Url(b64);
  const header = V.decodeCiphertext(raw);
  if (V.isCurrent('format', header.formatVersion) && V.isCurrent('aead', header.aeadId)) return b64;
  const plaintext = await V.open(key, raw, context);
  try {
    return V.toBase64Url(
      await V.seal(key, plaintext, context, { keyVersion: keyVersion, kdfId: header.kdfId }),
    );
  } finally {
    plaintext.fill(0);
  }
};

window.buildEntryMigrateItem = async function buildEntryMigrateItem(session, vault, row) {
  const V = window.vaultCrypto;
  const reseal = window.vaultReseal;
  const keyVersion = row.key_version || 1;
  const key = await session.openEntryKey(vault, row.uuid);
  const fields = {};
  const stored = (row.entry_fields || []).slice().sort(function (a, b) {
    return a.field_id < b.field_id ? -1 : 1;
  });
  for (const field of stored) {
    fields[field.field_id] = await reseal(
      V, key, field.encrypted_value, V.AD.entryFieldAd(row.uuid, field.field_id), keyVersion,
    );
  }
  const encryptedName = await reseal(
    V, key, row.encrypted_name, V.AD.entryFieldAd(row.uuid, 'name'), keyVersion,
  );
  const encryptedNotes = row.encrypted_notes
    ? await reseal(V, key, row.encrypted_notes, V.AD.entryFieldAd(row.uuid, 'notes'), keyVersion)
    : '';
  const payload = V.entryMetadataPayload({
    entry_uuid: row.uuid,
    vault_uuid: vault.uuid,
    signer_account_uuid: session.accountUuid(),
    entry_type: row.type,
    folder_uuid: row.folder || null,
    encrypted_name: encryptedName,
    encrypted_notes: encryptedNotes,
    key_version: keyVersion,
    entry_version: row.entry_version || 1,
    is_favorite: !!row.is_favorite,
    tag_uuids: [...(row.tags || [])],
    fields: fields,
  });
  return {
    kind: 'entry',
    uuid: row.uuid,
    encrypted_name: encryptedName,
    encrypted_notes: encryptedNotes,
    fields: fields,
    metadata_sig: await session.sign(payload),
    expected_sig: row.metadata_sig,
  };
};

window.buildFolderMigrateItem = async function buildFolderMigrateItem(session, vault, row) {
  const V = window.vaultCrypto;
  const key = await session.openVaultKey(vault);
  const encryptedName = await window.vaultReseal(
    V, key, row.encrypted_name, V.AD.folderFieldAd(row.uuid, 'name'), vault.key_version || 1,
  );
  const payload = V.folderMetadataPayload({
    folder_uuid: row.uuid,
    vault_uuid: vault.uuid,
    signer_account_uuid: session.accountUuid(),
    parent_uuid: row.parent || null,
    encrypted_name: encryptedName,
    position: row.position || 0,
  });
  return {
    kind: 'folder',
    uuid: row.uuid,
    encrypted_name: encryptedName,
    metadata_sig: await session.sign(payload),
    expected_sig: row.metadata_sig,
  };
};

window.buildTagMigrateItem = async function buildTagMigrateItem(session, vault, row) {
  const V = window.vaultCrypto;
  const key = await session.openVaultKey(vault);
  const encryptedName = await window.vaultReseal(
    V, key, row.encrypted_name, V.AD.tagFieldAd(row.uuid, 'name'), vault.key_version || 1,
  );
  const payload = V.tagMetadataPayload({
    tag_uuid: row.uuid,
    vault_uuid: vault.uuid,
    signer_account_uuid: session.accountUuid(),
    encrypted_name: encryptedName,
    color: row.color,
  });
  return {
    kind: 'tag',
    uuid: row.uuid,
    encrypted_name: encryptedName,
    metadata_sig: await session.sign(payload),
    expected_sig: row.metadata_sig,
  };
};

// The vault payload names its owner, and only the owner may rewrite it, so the
// signer is the session's account exactly as in the metadata write path.
window.buildVaultMetadataMigrateItem = async function buildVaultMetadataMigrateItem(session, vault) {
  const V = window.vaultCrypto;
  const key = await session.openVaultKey(vault);
  const keyVersion = vault.key_version || 1;
  const encryptedName = await window.vaultReseal(
    V, key, vault.encrypted_name, V.AD.vaultFieldAd(vault.uuid, 'name'), keyVersion,
  );
  const encryptedDescription = vault.encrypted_description
    ? await window.vaultReseal(
        V, key, vault.encrypted_description, V.AD.vaultFieldAd(vault.uuid, 'description'), keyVersion,
      )
    : '';
  const payload = V.vaultMetadataPayload({
    vault_uuid: vault.uuid,
    owner_account_uuid: session.accountUuid(),
    encrypted_name: encryptedName,
    encrypted_description: encryptedDescription,
    icon: vault.icon,
    color: vault.color,
    key_version: keyVersion,
    is_favorite: !!vault.is_favorite,
  });
  return {
    kind: 'metadata',
    encrypted_name: encryptedName,
    encrypted_description: encryptedDescription,
    metadata_sig: await session.sign(payload),
    expected_sig: vault.metadata_sig,
  };
};

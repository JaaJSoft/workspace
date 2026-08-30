// Writing a folder: a name sealed under the vault's metadata key, and the
// whole record signed.
//
// A folder's name travels under the *vault* key rather than a per-folder one:
// there is no folder key in the scheme, and the tree has to be openable by
// anyone who can open the vault. Its parent and its position are in the clear
// but inside the signature, which is what stops a server reshaping the tree
// without being seen.
window.buildFolderWriteRequest = async function buildFolderWriteRequest(
  session,
  vault,
  draft,
) {
  const V = window.vaultCrypto;
  const key = await session.openVaultKey(vault.uuid, vault.wrapped_key);
  const encryptedName = V.toBase64Url(
    await V.seal(
      key,
      new TextEncoder().encode(draft.name),
      V.AD.folderFieldAd(draft.uuid, 'name'),
      { keyVersion: vault.key_version || 1, kdfId: V.KDF_HKDF_SHA256 },
    ),
  );
  const position = draft.position || 0;
  const payload = V.folderMetadataPayload({
    folder_uuid: draft.uuid,
    vault_uuid: vault.uuid,
    signer_account_uuid: session.accountUuid(),
    parent_uuid: draft.parent || null,
    encrypted_name: encryptedName,
    position: position,
  });
  return {
    uuid: draft.uuid,
    vault: vault.uuid,
    parent: draft.parent || null,
    encrypted_name: encryptedName,
    position: position,
    metadata_sig: await session.sign(payload),
  };
};

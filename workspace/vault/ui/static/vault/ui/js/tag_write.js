// Writing a tag: a name sealed under the vault's metadata key, and the whole
// record signed.
//
// Like a folder, a tag has no key of its own - its name travels under the
// vault key so that anyone who can open the vault can read the labels in it.
// The colour is stored in the clear and covered by the signature: it is a hex
// from the shared palette, worth nothing to an observer, and inside the
// signature so a server cannot recolour a tag without being seen.
window.buildTagWriteRequest = async function buildTagWriteRequest(
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
      V.AD.tagFieldAd(draft.uuid, 'name'),
      { keyVersion: vault.key_version || 1, kdfId: V.KDF_HKDF_SHA256 },
    ),
  );
  // The palette's "None" is an empty string, which the column's vocabulary
  // does not include - it accepts a hex or a role name. `neutral` is the
  // model's own default and the role a tag-chip draws with no fill, so it is
  // what "no colour" means here rather than a value the write would refuse.
  const color = draft.color || 'neutral';
  const payload = V.tagMetadataPayload({
    tag_uuid: draft.uuid,
    vault_uuid: vault.uuid,
    signer_account_uuid: session.accountUuid(),
    encrypted_name: encryptedName,
    color: color,
  });
  return {
    uuid: draft.uuid,
    vault: vault.uuid,
    encrypted_name: encryptedName,
    color: color,
    metadata_sig: await session.sign(payload),
  };
};

// Rewriting a vault's own metadata: its name, its icon and colour, whether
// it is a favourite.
//
// Every one of those fields sits inside the signed payload, so none of them
// can be written on its own - there is no partial update of a signed record,
// only a whole one re-signed. That is also why the server refuses to help: it
// would have to forge the account's signature to do it.
//
// The metadata key is opened per call and never held: openVaultKey derives it
// from the wrapped key each time, and it is non-extractable, so there is no
// copy on this side to clear afterwards.
window.buildVaultUpdateRequest = async function buildVaultUpdateRequest(
  session,
  vault,
  changes,
) {
  const V = window.vaultCrypto;
  const next = Object.assign(
    {
      name: vault.name,
      icon: vault.icon || 'lock',
      color: vault.color || 'primary',
      is_favorite: !!vault.is_favorite,
    },
    changes || {},
  );
  const rewritesDescription = Object.prototype.hasOwnProperty.call(
    changes || {}, 'description'
  );

  // Re-sealed rather than carried over, because the name is what a rename
  // changes; sealing an unchanged name costs one AEAD call and keeps this
  // function with a single path instead of two that can disagree.
  const metaKey = await session.openVaultKey(vault.uuid, vault.wrapped_key);
  const encryptedName = V.toBase64Url(
    await V.seal(
      metaKey,
      new TextEncoder().encode(next.name),
      V.AD.vaultFieldAd(vault.uuid, 'name'),
      { keyVersion: vault.key_version || 1, kdfId: V.KDF_HKDF_SHA256 },
    ),
  );

  // Carried as the ciphertext it already is unless the caller changed it:
  // re-sealing an unchanged description would spend a key operation to store
  // the same plaintext under a new nonce, and the signature covers the
  // ciphertext either way.
  let encryptedDescription = vault.encrypted_description || '';
  if (rewritesDescription) {
    encryptedDescription = next.description
      ? V.toBase64Url(
          await V.seal(
            metaKey,
            new TextEncoder().encode(next.description),
            V.AD.vaultFieldAd(vault.uuid, 'description'),
            { keyVersion: vault.key_version || 1, kdfId: V.KDF_HKDF_SHA256 },
          ),
        )
      : '';
  }

  const payload = V.vaultMetadataPayload({
    vault_uuid: vault.uuid,
    // The vault payload names its owner, not whoever signs: a member may
    // open the vault and may not re-describe it, which is why every action
    // built on this request is owner-only server-side too.
    owner_account_uuid: session.accountUuid(),
    encrypted_name: encryptedName,
    encrypted_description: encryptedDescription,
    icon: next.icon,
    color: next.color,
    key_version: vault.key_version || 1,
    is_favorite: next.is_favorite,
  });

  return {
    encrypted_name: encryptedName,
    encrypted_description: encryptedDescription,
    icon: next.icon,
    color: next.color,
    is_favorite: next.is_favorite,
    metadata_sig: await session.sign(payload),
  };
};

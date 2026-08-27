// The sealing sequence for a brand-new vault: draw a key, derive a metadata
// key from it, encrypt the name under that, seal the vault key to a
// recipient's key-exchange public key with HPKE, and sign the metadata.
//
// Sealing a vault key to yourself and sharing it with someone else are the
// same operation with a different recipient - which is why sharing will add
// rows and change nothing here.
//
// vaultUuid is the caller's to keep: it is the key the server's conflict
// branch matches on, so a retry after a lost answer has to carry the one the
// first attempt sent or it describes a different vault.
window.buildVaultCreateRequest = async function buildVaultCreateRequest(session, name, vaultUuid) {
  const V = window.vaultCrypto;
  vaultUuid = vaultUuid || V.uuidV7();
  const vaultKey = V.randomBytes(32);
  const metaKey = await V.hkdf(vaultKey, V.AD.vaultMetaInfo(vaultUuid));
  const encryptedName = V.toBase64Url(
    await V.seal(metaKey, new TextEncoder().encode(name), V.AD.vaultFieldAd(vaultUuid, 'name'), {
      keyVersion: 1,
      kdfId: V.KDF_HKDF_SHA256,
    })
  );
  metaKey.fill(0);

  const wrapped = V.toBase64Url(
    await V.hpkeSeal(
      session.accountKexPublicRaw(),
      V.AD.vaultKeyInfo(vaultUuid, session.accountUuid()),
      vaultKey
    )
  );
  vaultKey.fill(0);

  const payload = V.vaultMetadataPayload({
    vault_uuid: vaultUuid,
    owner_account_uuid: session.accountUuid(),
    encrypted_name: encryptedName,
    encrypted_description: '',
    icon: 'lock',
    color: 'primary',
    key_version: 1,
    is_favorite: false,
  });

  return {
    uuid: vaultUuid,
    encrypted_name: encryptedName,
    encrypted_description: '',
    icon: 'lock',
    color: 'primary',
    metadata_sig: await session.sign(payload),
    wrapped_key: wrapped,
    hpke_suite: V.HPKE_SUITE_V1,
  };
};

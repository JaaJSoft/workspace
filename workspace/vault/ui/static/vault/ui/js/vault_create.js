// The sealing sequence for a brand-new vault: draw a key, derive a metadata
// key from it, encrypt the name and the description under that, seal the vault
// key to a recipient's key-exchange public key with HPKE, and sign the
// metadata.
//
// Sealing a vault key to yourself and sharing it with someone else are the
// same operation with a different recipient - which is why sharing will add
// rows and change nothing here.
//
// vaultUuid is the caller's to keep: it is the key the server's conflict
// branch matches on, so a retry after a lost answer has to carry the one the
// first attempt sent or it describes a different vault.
window.buildVaultCreateRequest = async function buildVaultCreateRequest(
  session,
  draft,
  vaultUuid,
) {
  const V = window.vaultCrypto;
  vaultUuid = vaultUuid || V.uuidV7();
  const icon = draft.icon || 'lock';
  const color = draft.color || 'primary';

  const vaultKey = V.randomBytes(32);
  const metaKey = await V.hkdf(vaultKey, V.AD.vaultMetaInfo(vaultUuid));
  const seal = async (text, field) =>
    V.toBase64Url(
      await V.seal(metaKey, new TextEncoder().encode(text), V.AD.vaultFieldAd(vaultUuid, field), {
        keyVersion: 1,
        kdfId: V.KDF_HKDF_SHA256,
      })
    );

  const encryptedName = await seal(draft.name, 'name');
  // An absent description stays an empty string rather than a ciphertext that
  // decrypts to nothing: the column takes one, and the reader knows to skip it.
  const encryptedDescription = draft.description ? await seal(draft.description, 'description') : '';
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
    encrypted_description: encryptedDescription,
    icon: icon,
    color: color,
    key_version: 1,
    // The server sets this one at creation and says so; signing anything
    // else fails verification. A vault created as a favourite is favourited
    // by the update that follows, which does accept it.
    is_favorite: false,
  });

  return {
    uuid: vaultUuid,
    encrypted_name: encryptedName,
    encrypted_description: encryptedDescription,
    icon: icon,
    color: color,
    metadata_sig: await session.sign(payload),
    wrapped_key: wrapped,
    hpke_suite: V.HPKE_SUITE_V1,
  };
};

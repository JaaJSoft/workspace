"""The page scripts the compatibility corpus is written and read with.

READ_EVERYTHING lives here rather than beside either caller because both ends
need the identical text: the generation walk lays the account out with it, and
the browser replay reopens a restored copy with it. A copy on each side would
let the two drift apart silently - and a manifest compared against a reader
that has quietly changed is a green run that proves nothing. The two write
scripts keep it company because they belong to the same walk.

Neither script goes through a form. That is deliberate and it is the narrow
part: the session they run on is the one the UI just opened, so the keys, the
associated data and the signatures are the product's own. Only the widget that
would have collected the values is missing.
"""

# What WRITE_NOTED_ENTRY below seals, transcribed so the generator can hold
# its manifest against it. A transcription and not the source: the script
# carries the literals, and the generator's value guard comparing the two is
# exactly what catches them drifting apart.
NOTED_ENTRY = {
    "name": "GitHub",
    "notes": "Sauvegarde : cle\u0301 range\u0301e a\u0300 la cave (NFD)",
    "fields": {
        "username": "octocat",
        "password": "hunter2",
        "custom:pin": "1234",
    },
}

# WRITE_ENTRY from test_entry_browser.py with two changes and nothing else:
# encrypted_notes is sealed under entryFieldAd(entryUuid, 'notes') instead of
# being the empty string, and the 'custom:pin' field of the original is kept.
# Copied rather than imported so the corpus stops moving the day that test is
# rewritten - a corpus whose writer follows the code it is meant to outlive
# guards nothing.
#
# The notes are non-ASCII and in NFD (a base letter followed by a combining
# mark, written here as an escape so this file stays ASCII). A reader that
# normalises on the way in or out would hand back the same text in NFC, which
# is a different byte string and a different seal - and every equality check
# that compared rendered strings would still pass.
WRITE_NOTED_ENTRY = """
async () => {
  const V = window.vaultCrypto, A = window.vaultApi, S = window.vaultSession;
  const enc = new TextEncoder();
  try {
    const vault = (await A.listVaults())[0];
    const entryUuid = V.uuidV7();
    const entryKey = await S.openEntryKey(vault.uuid, vault.wrapped_key, entryUuid);
    const seal = async (field, text) => V.toBase64Url(
      await V.seal(entryKey, enc.encode(text), V.AD.entryFieldAd(entryUuid, field), {
        keyVersion: 1,
        kdfId: V.KDF_HKDF_SHA256,
      })
    );

    const fields = {
      username: await seal('username', 'octocat'),
      password: await seal('password', 'hunter2'),
      'custom:pin': await seal('custom:pin', '1234'),
    };
    const encryptedName = await seal('name', 'GitHub');
    const encryptedNotes = await seal(
      'notes',
      'Sauvegarde : cle\\u0301 range\\u0301e a\\u0300 la cave (NFD)'
    );
    const payload = V.entryMetadataPayload({
      entry_uuid: entryUuid,
      vault_uuid: vault.uuid,
      signer_account_uuid: S.accountUuid(),
      entry_type: 'login',
      folder_uuid: null,
      encrypted_name: encryptedName,
      encrypted_notes: encryptedNotes,
      key_version: 1,
      entry_version: 1,
      is_favorite: false,
      tag_uuids: [],
      fields,
    });
    const created = await A.createEntry({
      uuid: entryUuid,
      vault: vault.uuid,
      type: 'login',
      folder: null,
      tags: [],
      is_favorite: false,
      encrypted_name: encryptedName,
      encrypted_notes: encryptedNotes,
      fields,
      metadata_sig: await S.sign(payload),
    });
    return { status: 201, uuid: created.uuid, vault: vault.uuid };
  } catch (error) {
    return { status: error.status || 0, reason: String(error && error.message) };
  }
}
"""

# Everything the account holds, opened and laid out in one shape.
#
# Sorted by UUID at every level, and fields by identifier: the listings order
# by created_at, which two rows written in the same millisecond share. A
# manifest that reordered between two runs would read as a format change.
#
# Nothing is swallowed. An open that fails raises out of page.evaluate and
# lands in the caller as an error, because the one outcome this must never
# produce is a short manifest that still parses.
READ_EVERYTHING = """
async () => {
  const V = window.vaultCrypto, A = window.vaultApi, S = window.vaultSession;
  const dec = new TextDecoder();
  const openText = async (key, value, ad) =>
    dec.decode(await V.open(key, V.fromBase64Url(value), ad));
  const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const byUuid = (a, b) => byText(a.uuid, b.uuid);

  const vaults = [];
  for (const vault of (await A.listVaults()).slice().sort(byUuid)) {
    const metaKey = await S.openVaultKey(vault.uuid, vault.wrapped_key);

    const folders = [];
    for (const folder of (await A.listFolders(vault.uuid)).slice().sort(byUuid)) {
      folders.push({
        uuid: folder.uuid,
        parent: folder.parent,
        position: folder.position,
        name: await openText(
          metaKey, folder.encrypted_name, V.AD.folderFieldAd(folder.uuid, 'name')
        ),
      });
    }

    const tags = [];
    for (const tag of (await A.listTags(vault.uuid)).slice().sort(byUuid)) {
      tags.push({
        uuid: tag.uuid,
        color: tag.color,
        name: await openText(
          metaKey, tag.encrypted_name, V.AD.tagFieldAd(tag.uuid, 'name')
        ),
      });
    }

    // Both listings: the trash is a separate view, so an account read from
    // the default one alone would lose every trashed row without saying so.
    const rows = [
      ...(await A.listEntries(vault.uuid)),
      ...(await A.listEntries(vault.uuid, { trashed: true })),
    ].sort(byUuid);

    const entries = [];
    for (const row of rows) {
      const entryKey = await S.openEntryKey(vault.uuid, vault.wrapped_key, row.uuid);
      const fields = {};
      const fieldRows = row.entry_fields
        .slice()
        .sort((a, b) => byText(a.field_id, b.field_id));
      for (const field of fieldRows) {
        fields[field.field_id] = await openText(
          entryKey, field.encrypted_value, V.AD.entryFieldAd(row.uuid, field.field_id)
        );
      }
      entries.push({
        uuid: row.uuid,
        type: row.type,
        folder: row.folder,
        tags: [...row.tags].sort(byText),
        is_favorite: row.is_favorite,
        trashed: row.deleted_at !== null,
        key_version: row.key_version,
        entry_version: row.entry_version,
        name: await openText(
          entryKey, row.encrypted_name, V.AD.entryFieldAd(row.uuid, 'name')
        ),
        notes: row.encrypted_notes
          ? await openText(
              entryKey, row.encrypted_notes, V.AD.entryFieldAd(row.uuid, 'notes')
            )
          : '',
        fields,
      });
    }

    vaults.push({
      uuid: vault.uuid,
      icon: vault.icon,
      color: vault.color,
      is_favorite: vault.is_favorite,
      key_version: vault.key_version,
      name: await openText(
        metaKey, vault.encrypted_name, V.AD.vaultFieldAd(vault.uuid, 'name')
      ),
      description: vault.encrypted_description
        ? await openText(
            metaKey,
            vault.encrypted_description,
            V.AD.vaultFieldAd(vault.uuid, 'description')
          )
        : '',
      folders,
      tags,
      entries,
    });
  }
  return { account_uuid: S.accountUuid(), vaults };
}
"""

# A description on the vault onboarding made.
#
# The creation dialog offers the field and the corpus uses it for every vault
# it creates itself, but the first vault is not created there: onboarding
# writes `{ name: 'Personal' }` with no description, and neither the rename
# dialog nor the appearance one renders the field afterwards. So the one vault
# every real account has is the one vault no sequence of clicks can describe.
#
# Left empty it would take `v1|vault-field|<uuid>|description` out of the
# corpus on that row, which is the hole this closes. buildVaultUpdateRequest
# is the product's own helper - the same one the rename dialog calls - handed
# the field that dialog does not draw: the form is bypassed, never the crypto.
#
# The name has to be opened before it can be re-sealed: the helper takes a
# vault as the browser holds it, with a decrypted `name`, and a row straight
# off the wire carries `encrypted_name` instead.
DESCRIBE_FIRST_VAULT = """
async (description) => {
  const V = window.vaultCrypto, A = window.vaultApi, S = window.vaultSession;
  try {
    const row = (await A.listVaults())[0];
    const metaKey = await S.openVaultKey(row.uuid, row.wrapped_key);
    const name = new TextDecoder().decode(
      await V.open(
        metaKey,
        V.fromBase64Url(row.encrypted_name),
        V.AD.vaultFieldAd(row.uuid, 'name')
      )
    );
    const body = await window.buildVaultUpdateRequest(
      S,
      Object.assign({}, row, { name }),
      { description }
    );
    await A.updateVault(row.uuid, body);
    return { status: 200, uuid: row.uuid, name };
  } catch (error) {
    return { status: error.status || 0, reason: String(error && error.message) };
  }
}
"""

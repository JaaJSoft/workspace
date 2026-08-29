// Turning what the server stores into what the browser may show.
//
// Two rules shape every function here, and both are security properties
// rather than preferences:
//
// 1. **A row is verified before it is read.** A signature that does not check
//    means the row leaves the listing entirely - not a partial render, not a
//    name shown "just to help identify it". The count is all that survives,
//    and the banner is built from it.
//
// 2. **Only a name and a login are opened.** A password, an authenticator key
//    or any other secret field stays sealed until the moment it is copied, so
//    that a page sitting open holds no secret in component state where the
//    developer tools would show it. `openField` is that moment, and it hands
//    the value back without keeping a copy.
window.vaultReader = (function () {
  const NAME_FIELD = 'name';
  const DESCRIPTION_FIELD = 'description';
  // What the listing is allowed to open. Deliberately a list rather than a
  // "not secret" test: adding a field to a type must not silently widen what
  // a listing decrypts.
  const LISTED_FIELDS = ['username'];

  function fieldMap(row) {
    const fields = {};
    (row.entry_fields || []).forEach(function (field) {
      fields[field.field_id] = field.encrypted_value;
    });
    return fields;
  }

  async function openText(V, key, ciphertext, associatedData) {
    const plaintext = await V.open(key, V.fromBase64Url(ciphertext), associatedData);
    return new TextDecoder().decode(plaintext);
  }

  async function readEntry(session, vault, row) {
    const V = window.vaultCrypto;
    const fields = fieldMap(row);
    const payload = V.entryMetadataPayload({
      entry_uuid: row.uuid,
      vault_uuid: row.vault,
      // The entry payload names its signer, not the vault's owner: in v2 a
      // member signs an entry they do not own, and this is where that shows.
      signer_account_uuid: session.accountUuid(),
      entry_type: row.type,
      folder_uuid: row.folder,
      encrypted_name: row.encrypted_name,
      encrypted_notes: row.encrypted_notes,
      key_version: row.key_version,
      entry_version: row.entry_version,
      is_favorite: row.is_favorite,
      tag_uuids: row.tags || [],
      fields: fields,
    });
    await session.verifyRecord(payload, row.metadata_sig, 'entry-metadata');

    const key = await session.openEntryKey(vault.uuid, vault.wrapped_key, row.uuid);
    const entry = {
      uuid: row.uuid,
      type: row.type,
      folder: row.folder,
      tags: row.tags || [],
      favorite: row.is_favorite,
      trashed: !!row.deleted_at,
      modified: row.updated_at,
      created: row.created_at,
      // Which fields the row carries, so the browser can tell a login with no
      // authenticator key from one that has one without opening either.
      fieldIds: Object.keys(fields),
      name: await openText(V, key, row.encrypted_name, V.AD.entryFieldAd(row.uuid, NAME_FIELD)),
      username: '',
    };
    for (const fieldId of LISTED_FIELDS) {
      if (fields[fieldId]) {
        entry[fieldId] = await openText(
          V, key, fields[fieldId], V.AD.entryFieldAd(row.uuid, fieldId)
        );
      }
    }
    return entry;
  }

  async function readFolder(session, vault, row) {
    const V = window.vaultCrypto;
    const payload = V.folderMetadataPayload({
      folder_uuid: row.uuid,
      vault_uuid: row.vault,
      signer_account_uuid: session.accountUuid(),
      parent_uuid: row.parent,
      encrypted_name: row.encrypted_name,
      position: row.position,
    });
    await session.verifyRecord(payload, row.metadata_sig, 'folder-metadata');
    const key = await session.openVaultKey(vault.uuid, vault.wrapped_key);
    return {
      uuid: row.uuid,
      parent: row.parent,
      position: row.position,
      name: await openText(V, key, row.encrypted_name, V.AD.folderFieldAd(row.uuid, NAME_FIELD)),
    };
  }

  async function readTag(session, vault, row) {
    const V = window.vaultCrypto;
    const payload = V.tagMetadataPayload({
      tag_uuid: row.uuid,
      vault_uuid: row.vault,
      signer_account_uuid: session.accountUuid(),
      encrypted_name: row.encrypted_name,
      color: row.color,
    });
    await session.verifyRecord(payload, row.metadata_sig, 'tag-metadata');
    const key = await session.openVaultKey(vault.uuid, vault.wrapped_key);
    return {
      uuid: row.uuid,
      color: row.color,
      name: await openText(V, key, row.encrypted_name, V.AD.tagFieldAd(row.uuid, NAME_FIELD)),
    };
  }

  // One failed row must not cost the others their listing, so each is read on
  // its own and a failure is counted. A lock is the one exception: it is not
  // tampering, and reporting it as such would tell the user to distrust a
  // vault that is merely closed.
  async function readAll(rows, read) {
    const results = [];
    let tamperedCount = 0;
    for (const row of rows) {
      try {
        results.push(await read(row));
      } catch (err) {
        if (err && err.reason === 'locked') throw err;
        tamperedCount += 1;
      }
    }
    return { rows: results, tamperedCount: tamperedCount };
  }

  // A vault's own metadata: verified, then its name opened. Both screens
  // read a vault this way, and the three degraded outcomes below are the
  // reason it is one function - a second copy would drift on which of them
  // still shows a name.
  async function readVault(session, row) {
    const V = window.vaultCrypto;
    const payload = V.vaultMetadataPayload(
      Object.assign({}, row, { vault_uuid: row.uuid })
    );
    try {
      await session.verifyVaultMetadata(payload, row.metadata_sig);
    } catch (err) {
      // A lock landing mid-listing fails this the same way a forged signature
      // does, and the tamper alert is the one message the user is told to act
      // on rather than retry - so it must never stand in for an idle timeout.
      if (err && err.reason === 'locked') throw err;
      // Signed by nobody the account trusts: never shown with a name that
      // came along for the ride.
      return Object.assign({}, row, { tampered: true, name: '', description: '' });
    }
    if (!row.wrapped_key) {
      return Object.assign({}, row, { unopenable: true, name: '', description: '' });
    }
    try {
      const metaKey = await session.openVaultKey(row.uuid, row.wrapped_key);
      return Object.assign({}, row, {
        name: await openText(
          V, metaKey, row.encrypted_name, V.AD.vaultFieldAd(row.uuid, NAME_FIELD)
        ),
        // Every vault written before the field was offered stores an empty
        // string, and opening one would throw where nothing is wrong.
        description: row.encrypted_description
          ? await openText(
              V, metaKey, row.encrypted_description,
              V.AD.vaultFieldAd(row.uuid, DESCRIPTION_FIELD)
            )
          : '',
      });
    } catch (err) {
      if (err && err.reason === 'locked') throw err;
      // Localised the same way a bad signature is: one row loses its name and
      // the rest of the listing keeps going.
      return Object.assign({}, row, { unreadable: true, name: '', description: '' });
    }
  }

  return {
    readVault: readVault,
    readEntries: function (session, vault, rows) {
      return readAll(rows, function (row) { return readEntry(session, vault, row); });
    },
    readFolders: function (session, vault, rows) {
      return readAll(rows, function (row) { return readFolder(session, vault, row); });
    },
    readTags: function (session, vault, rows) {
      return readAll(rows, function (row) { return readTag(session, vault, row); });
    },

    // The other half of lazy decryption: one field, opened when it is asked
    // for, handed straight to the caller. Nothing here stores it.
    openField: async function (session, vault, row, fieldId) {
      const V = window.vaultCrypto;
      const ciphertext = fieldMap(row)[fieldId];
      if (!ciphertext) return '';
      const key = await session.openEntryKey(vault.uuid, vault.wrapped_key, row.uuid);
      return openText(V, key, ciphertext, V.AD.entryFieldAd(row.uuid, fieldId));
    },
  };
})();

// Reading the whole account into one plaintext tree. This is the only place
// in the module that decrypts everything a user has, which is why it never
// hands its result to anything but the sealer, and why it refuses outright
// rather than skipping what it cannot read.
function VaultExportError(message, reason) {
  const error = new Error(message);
  error.name = 'VaultExportError';
  error.reason = reason;
  return error;
}
window.VaultExportError = VaultExportError;

window.vaultExportTree = (function () {
  // One entry key per entry, then every field opened with it. openField would
  // re-derive the key for each field, which is an HPKE open and an HKDF per
  // password on an account with thousands of them.
  async function openEntryContent(session, vault, row) {
    const V = window.vaultCrypto;
    const key = await session.openEntryKey(vault.uuid, vault.wrapped_key, row.uuid);
    const decode = async (ciphertext, fieldId) => new TextDecoder().decode(
      await V.open(key, V.fromBase64Url(ciphertext), V.AD.entryFieldAd(row.uuid, fieldId))
    );
    const fields = {};
    for (const field of row.entry_fields || []) {
      fields[field.field_id] = await decode(field.encrypted_value, field.field_id);
    }
    return {
      type: row.type,
      name: await decode(row.encrypted_name, 'name'),
      // Written as an empty string by every entry created without one, and
      // opening that would throw where nothing is wrong.
      notes: row.encrypted_notes ? await decode(row.encrypted_notes, 'notes') : '',
      favorite: !!row.is_favorite,
      trashed: !!row.deleted_at,
      created_at: row.created_at,
      updated_at: row.updated_at,
      last_used_at: row.last_used_at,
      fields: fields,
    };
  }

  // A signature that does not verify, or a field that will not open, means the
  // archive is not written at all. vaultReader counts those rather than
  // throwing, so the count is what we read.
  function refuseIfUnreadable(...results) {
    const total = results.reduce((sum, result) => sum + result.tamperedCount, 0);
    if (total > 0) {
      throw VaultExportError(
        `${total} row(s) could not be verified or opened`, 'unreadable'
      );
    }
  }

  async function buildVault(session, vaultRow, onProgress) {
    const api = window.vaultApi;
    const reader = window.vaultReader;
    const vault = await reader.readVault(session, vaultRow);
    if (vault.tampered || vault.unopenable || vault.unreadable) {
      throw VaultExportError('a vault could not be verified or opened', 'unreadable');
    }
    const [folderRows, tagRows, liveRows, trashedRows] = await Promise.all([
      api.listFolders(vaultRow.uuid),
      api.listTags(vaultRow.uuid),
      api.listEntries(vaultRow.uuid),
      api.listEntries(vaultRow.uuid, { trashed: true }),
    ]);
    const rows = liveRows.concat(trashedRows);
    const folders = await reader.readFolders(session, vaultRow, folderRows);
    const tags = await reader.readTags(session, vaultRow, tagRows);
    const entries = await reader.readEntries(session, vaultRow, rows);
    refuseIfUnreadable(folders, tags, entries);

    // Local ids, scoped to the vault: nothing in the file correlates to
    // anything outside the file.
    const folderId = new Map(folders.rows.map((row, index) => [row.uuid, index]));
    const tagId = new Map(tags.rows.map((row, index) => [row.uuid, index]));

    const built = [];
    for (const row of rows) {
      const content = await openEntryContent(session, vaultRow, row);
      built.push(Object.assign(content, {
        folder: row.folder === null || row.folder === undefined
          ? null
          : folderId.get(row.folder) ?? null,   // ?? null: a dangling reference would put `undefined` into the CBOR tree
        tags: (row.tags || []).map((uuid) => tagId.get(uuid)).filter((id) => id !== undefined),
      }));
      if (onProgress) onProgress();
    }

    return {
      name: vault.name,
      description: vault.description,
      icon: vaultRow.icon,
      color: vaultRow.color,
      is_favorite: !!vaultRow.is_favorite,
      folders: folders.rows.map((row) => ({
        id: folderId.get(row.uuid),
        parent: row.parent === null || row.parent === undefined
          ? null
          : folderId.get(row.parent) ?? null,
        name: row.name,
        position: row.position,
      })),
      tags: tags.rows.map((row) => ({
        id: tagId.get(row.uuid), name: row.name, color: row.color,
      })),
      entries: built,
    };
  }

  return {
    buildTree: async function (session, { onProgress } = {}) {
      const vaultRows = await window.vaultApi.listVaults();
      if (!vaultRows.length) {
        throw VaultExportError('this account holds no vault', 'empty');
      }
      const vaults = [];
      for (const vaultRow of vaultRows) {
        vaults.push(await buildVault(session, vaultRow, onProgress));
      }
      return {
        format: 'vault-archive',
        version: 1,
        exported_at: new Date().toISOString(),
        vaults: vaults,
      };
    },
  };
})();

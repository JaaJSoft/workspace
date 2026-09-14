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

window.vaultExportTree = (function () {
  // Every field is opened through the reader, which owns how an entry field is
  // opened and under which slot; buildTree holds the entry-key cache, so the
  // key is derived once per entry, not once per field. What this adds is the
  // refusal. vaultReader verifies `name` and `username` only, so a corrupted
  // password, totp, uri or note reaches this pass with the tamper count still
  // clean. Left bare, that failure surfaces as an AEAD rejection carrying no
  // reason and the dialog says "the export failed" - for the likeliest
  // tampering target of all. No retry with other associated data, and no
  // skipping: the field id is not named either, since a custom one is a
  // string the user wrote.
  async function openEntryContent(session, vault, row) {
    const open = async (fieldId) => {
      try {
        return await window.vaultReader.openField(session, vault, row, fieldId);
      } catch (err) {
        // A lock is not tampering, and the dialog says so in its own words.
        if (err && err.reason === 'locked') throw err;
        throw VaultExportError('a field could not be opened', 'unreadable');
      }
    };
    const fields = {};
    for (const field of row.entry_fields || []) {
      fields[field.field_id] = await open(field.field_id);
    }
    return {
      type: row.type,
      name: await open('name'),
      // An entry created without notes stores an empty column, which the
      // reader answers with '' without opening anything.
      notes: await open('notes'),
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

  // Checked once per vault and once per entry. Cancelling a run cannot unwind
  // the walk it started, and without this the walk reads the rest of the
  // account into a tree nobody will look at.
  function stopIfAbandoned(stillCurrent) {
    if (stillCurrent && !stillCurrent()) {
      throw VaultExportError('the export was abandoned', 'cancelled');
    }
  }

  async function buildVault(session, vaultRow, { onProgress, stillCurrent }) {
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
      stopIfAbandoned(stillCurrent);
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
    buildTree: async function (session, { onProgress, stillCurrent } = {}) {
      const vaultRows = await window.vaultApi.listVaults();
      if (!vaultRows.length) {
        throw VaultExportError('this account holds no vault', 'empty');
      }
      // Every entry here is read twice - verified by the reader, then opened
      // for its content - and both passes ask for the same entry key. Held for
      // the length of the walk and dropped with it; a lock inside still throws
      // 'locked', which is what refuses the export rather than tampering.
      return session.withEntryKeyCache(async () => {
        const vaults = [];
        for (const vaultRow of vaultRows) {
          stopIfAbandoned(stillCurrent);
          vaults.push(await buildVault(session, vaultRow, { onProgress, stillCurrent }));
        }
        return {
          format: 'vault-archive',
          version: 1,
          exported_at: new Date().toISOString(),
          vaults: vaults,
        };
      });
    },
  };
})();

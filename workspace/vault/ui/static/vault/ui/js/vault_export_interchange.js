// Projecting the export tree onto the de-facto interchange format. Everything
// this drops, it drops because the target has nowhere to put it - and the one
// thing it transforms, it transforms because '/' is the target's nesting
// separator.
window.vaultExportInterchange = (function () {
  // A name is content; the separator is syntax. U+2215 reads the same and is
  // not the separator, so a vault called "Perso/Pro" stays one folder instead
  // of forging a level. This is deliberate and documented - do not restore '/'.
  const SEPARATOR = '/';
  const LOOKALIKE = '∕';
  const escapeName = (name) => String(name).split(SEPARATOR).join(LOOKALIKE);

  // Declared per entry type, never assumed. Only `login` exists today, so the
  // skipped count is always zero - and that is exactly why the branch is here:
  // a second type must not ship disguised as the first.
  const ITEM_TYPES = { login: 1 };

  function folderPath(vault, folder) {
    const parts = [escapeName(folder.name)];
    let current = folder;
    while (current.parent !== null && current.parent !== undefined) {
      current = vault.folders.find((candidate) => candidate.id === current.parent);
      if (!current) break;
      parts.unshift(escapeName(current.name));
    }
    parts.unshift(escapeName(vault.name));
    return parts.join(SEPARATOR);
  }

  function toBitwarden(tree) {
    const V = window.vaultCrypto;
    const folders = [];
    const items = [];
    let skipped = 0;

    tree.vaults.forEach((vault) => {
      const folderIds = new Map();
      // The vault itself, so an entry filed nowhere still lands under it.
      const vaultFolder = { id: V.uuidV7(), name: escapeName(vault.name) };
      folders.push(vaultFolder);
      vault.folders.forEach((folder) => {
        const row = { id: V.uuidV7(), name: folderPath(vault, folder) };
        folderIds.set(folder.id, row.id);
        folders.push(row);
      });
      const tagNames = new Map(vault.tags.map((tag) => [tag.id, tag.name]));

      vault.entries.forEach((entry) => {
        if (entry.trashed) return;
        const itemType = ITEM_TYPES[entry.type];
        if (itemType === undefined) {
          skipped += 1;
          return;
        }
        const uri = entry.fields.uri;
        items.push({
          id: V.uuidV7(),
          organizationId: null,
          folderId: entry.folder === null || entry.folder === undefined
            ? vaultFolder.id
            : folderIds.get(entry.folder),
          type: itemType,
          name: entry.name,
          notes: entry.notes || null,
          favorite: !!entry.favorite,
          login: {
            uris: uri ? [{ match: null, uri: uri }] : [],
            username: entry.fields.username || null,
            password: entry.fields.password || null,
            totp: entry.fields.totp || null,
          },
          // The target has one folder per item and no tags at all. Custom
          // fields are lossy but readable by a human on the other side, which
          // beats disappearing.
          fields: entry.tags
            .map((id) => tagNames.get(id))
            .filter((name) => name !== undefined)
            .map((name) => ({ name: 'tag', value: name, type: 0 })),
          collectionIds: null,
        });
      });
    });

    return { json: { encrypted: false, folders: folders, items: items }, skipped: skipped };
  }

  return {
    toBitwarden: toBitwarden,
    interchangeFilename: (date) => `vault-export-${date.toISOString().slice(0, 10)}.json`,
  };
})();

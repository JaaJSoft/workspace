// Dropping a tag or a folder without leaving a broken signature behind.
//
// Both removals change something the signature covers - a tag uuid, a folder
// uuid - on entries the user did not ask to touch. The server cannot fix
// those signatures: fixing one means producing it, and producing one means
// forging the account's. So the repair belongs here, and its order is the
// whole point:
//
//   re-sign every affected entry FIRST, then ask for the removal.
//
// The other order leaves rows whose signature covers a tag they no longer
// carry. Those rows read as tampered from then on - the loudest failure the
// scheme has, for a change nobody thought was dangerous.
window.vaultResign = (function () {
  // Every entry that would be left signing something the removal takes away.
  function carriers(entries, tagUuid) {
    return entries.filter(function (entry) {
      return (entry.tags || []).some(function (uuid) {
        return String(uuid) === String(tagUuid);
      });
    });
  }

  // Deepest first. VaultFolder.parent is CASCADE, so the server refuses a
  // folder that still has subfolders rather than silently taking folders -
  // and signatures - the client never named.
  function subtree(folders, rootUuid) {
    const children = function (uuid) {
      return folders.filter(function (folder) {
        return String(folder.parent) === String(uuid);
      });
    };
    const ordered = [];
    const walk = function (uuid) {
      children(uuid).forEach(function (child) { walk(child.uuid); });
      ordered.push(uuid);
    };
    walk(rootUuid);
    return ordered;
  }

  async function resignWithout(vault, row, changes) {
    const body = await window.buildEntryResignRequest(
      window.vaultSession, vault, row, changes
    );
    return window.vaultApi.updateEntry(row.uuid, body);
  }

  return {
    // Sequential, not parallel: a failure has to stop the ones after it, and
    // Promise.all would have already sent them.
    deleteTagSafely: async function (vault, tagUuid, entries) {
      for (const row of carriers(entries, tagUuid)) {
        await resignWithout(vault, row, {
          tags: (row.tags || []).filter(function (uuid) {
            return String(uuid) !== String(tagUuid);
          }),
        });
      }
      return window.vaultApi.deleteTag(tagUuid);
    },

    // One transactional request per folder, deepest first. The body carries
    // every entry the folder holds - trashed ones included, because
    // deleted_at is a view and folder_id is still a RESTRICT reference - each
    // re-signed with no folder. The server compares the submitted set against
    // the folder's real contents and refuses a mismatch.
    deleteFolderSafely: async function (vault, folderUuid, folders, entries) {
      for (const uuid of subtree(folders, folderUuid)) {
        const occupants = entries.filter(function (entry) {
          return String(entry.folder) === String(uuid);
        });
        const signed = [];
        for (const row of occupants) {
          const body = await window.buildEntryResignRequest(
            window.vaultSession, vault, row, { folder: null }
          );
          // The endpoint reads everything else from the row it already holds;
          // sending more would be sending a second copy to disagree with.
          signed.push({ uuid: body.uuid, metadata_sig: body.metadata_sig });
        }
        await window.vaultApi.deleteFolder(uuid, signed);
      }
    },
  };
})();

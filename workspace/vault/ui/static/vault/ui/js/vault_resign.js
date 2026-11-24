// Dropping a tag or a folder without leaving a broken signature behind.
//
// Both removals change something the signature covers - a tag uuid, a folder
// uuid - on entries the user did not ask to touch. The server cannot fix
// those signatures: fixing one means producing it, and producing one means
// forging the account's. So the repair belongs here, and it travels with the
// removal: the new signatures and the deletion are one request the server
// applies in one transaction, so rows signed over something they no longer
// carry - which read as tampered from then on - cannot be left behind.
window.vaultResign = (function () {
  // Every entry that would be left signing something the removal takes away.
  function carriers(entries, tagUuid) {
    return entries.filter(function (entry) {
      return (entry.tags || []).some(function (uuid) {
        return String(uuid) === String(tagUuid);
      });
    });
  }

  // The folder and every folder below it. VaultFolder.parent is CASCADE, so
  // the server refuses the deletion when the subfolders it would take with
  // the folder are not exactly the ones the client named.
  function subtree(folders, rootUuid) {
    const found = [String(rootUuid)];
    for (let index = 0; index < found.length; index++) {
      folders.forEach(function (folder) {
        if (String(folder.parent) === found[index] && !found.includes(String(folder.uuid))) {
          found.push(String(folder.uuid));
        }
      });
    }
    return found;
  }

  function Blocked() {
    const error = new Error('an affected entry could not be verified');
    error.name = 'VaultResignBlocked';
    return error;
  }

  function signaturePrefix(row) {
    return window.vaultCrypto.fromBase64Url(row.metadata_sig)[0];
  }

  // Refused before the first request: a removal that stopped half-way would
  // still have re-signed the rows it reached.
  function assertResignable(affected, unverified) {
    if (unverified.length) throw Blocked();
    for (const row of affected) {
      if (!window.vaultCrypto.mayResign(signaturePrefix(row))) throw Blocked();
    }
  }

  return {
    Blocked: Blocked,

    // Every write that re-signs a stored row asks this first. A signature
    // algorithm marked broken must never be re-signed by the account: the
    // new signature would vouch for whatever the server put in the row.
    assertMayResign: function (row) {
      if (!window.vaultCrypto.mayResign(signaturePrefix(row))) throw Blocked();
    },

    // One transactional request: the body carries every entry that holds the
    // tag - trashed ones included - each re-signed without it. The server
    // compares the set against the tag's real carriers and refuses a mismatch.
    deleteTagSafely: async function (vault, tagUuid, entries, unverified) {
      const affected = carriers(entries, tagUuid);
      assertResignable(affected, carriers(unverified || [], tagUuid));
      const signed = [];
      for (const row of affected) {
        const body = await window.buildEntryResignRequest(
          window.vaultSession, vault, row, {
            tags: (row.tags || []).filter(function (uuid) {
              return String(uuid) !== String(tagUuid);
            }),
          }
        );
        signed.push({ uuid: body.uuid, metadata_sig: body.metadata_sig });
      }
      return window.vaultApi.deleteTag(tagUuid, signed);
    },

    // One transactional request for the whole subtree. The body names every
    // subfolder and carries every entry they and the folder hold - trashed
    // ones included, because deleted_at is a view and folder_id is still a
    // RESTRICT reference - each re-signed with no folder. The server compares
    // both sets against the stored subtree and refuses a mismatch.
    deleteFolderSafely: async function (vault, folderUuid, folders, entries, unverified) {
      const levels = subtree(folders, folderUuid);
      const inSubtree = function (entry) { return levels.includes(String(entry.folder)); };
      const occupants = entries.filter(inSubtree);
      assertResignable(occupants, (unverified || []).filter(inSubtree));
      const signed = [];
      for (const row of occupants) {
        const body = await window.buildEntryResignRequest(
          window.vaultSession, vault, row, { folder: null }
        );
        // The endpoint reads everything else from the row it already holds;
        // sending more would be sending a second copy to disagree with.
        signed.push({ uuid: body.uuid, metadata_sig: body.metadata_sig });
      }
      return window.vaultApi.deleteFolder(folderUuid, levels.slice(1), signed);
    },
  };
})();

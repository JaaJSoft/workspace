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

    // One transactional request per folder, deepest first. The body carries
    // every entry the folder holds - trashed ones included, because
    // deleted_at is a view and folder_id is still a RESTRICT reference - each
    // re-signed with no folder. The server compares the submitted set against
    // the folder's real contents and refuses a mismatch.
    deleteFolderSafely: async function (vault, folderUuid, folders, entries, unverified) {
      const levels = subtree(folders, folderUuid).map(String);
      const inSubtree = function (entry) { return levels.includes(String(entry.folder)); };
      assertResignable(entries.filter(inSubtree), (unverified || []).filter(inSubtree));
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

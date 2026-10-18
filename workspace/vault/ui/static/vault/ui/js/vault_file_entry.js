// Filing entries: moving them between folders and changing their tags.
//
// Both are inside the signed payload, so both are a re-signature of the
// stored row and nothing else - no ciphertext is opened on the way. They
// share one write path, resignEntries, which decides what a batch is: the
// rows whose change is real, every one of them checked before the first
// request, written in order until the first failure.

window.vaultFileEntryMixin = function vaultFileEntryMixin() {
  // A set, not a list: the same tags in another order are the same tags, and
  // writing them again would change a signature and nothing the user sees.
  function sameTags(left, right) {
    const a = new Set((left || []).map(String));
    const b = new Set((right || []).map(String));
    if (a.size !== b.size) return false;
    for (const uuid of a) {
      if (!b.has(uuid)) return false;
    }
    return true;
  }

  function changesNothing(row, change) {
    if ('folder' in change && String(row.folder || null) !== String(change.folder || null)) {
      return false;
    }
    if ('tags' in change && !sameTags(row.tags, change.tags)) return false;
    return true;
  }

  return {
    // The move being chosen, or null: { uuids, target }. `target` stays
    // undefined until a place is picked, because null already means "no
    // folder".
    moveDialog: null,
    // The tags being edited, or null: { uuids, start, states }, both maps of
    // tag uuid to 'all' | 'some' | 'none'. `start` is what the selection
    // carried when the dialog opened; only the tags whose state moved from it
    // are written.
    tagsDialog: null,
    // What the last batch did, in words. It names a folder, so it goes when
    // the vault locks.
    fileNotice: '',

    openMoveDialog: function (rows) {
      this.moveDialog = {
        uuids: rows.map(function (row) { return row.uuid; }),
        target: undefined,
      };
    },

    openTagsDialog: function (rows) {
      const uuids = rows.map(function (row) { return row.uuid; });
      const start = this.tagStates(uuids);
      this.tagsDialog = { uuids: uuids, start: start, states: Object.assign({}, start) };
    },

    clearFileEntry: function () {
      this.moveDialog = null;
      this.tagsDialog = null;
      this.fileNotice = '';
    },

    // The one write path for both. Every row is checked before the first
    // request: a batch refused half-way would have re-signed the rows it
    // reached, and the user asked for all of them or none. A row whose change
    // equals what it already stores is no part of the batch - not written,
    // not counted and not checked, so a row that cannot be re-signed does not
    // block a move it would never take part in.
    resignEntries: async function (changesByUuid) {
      this.fileNotice = '';
      await this.flushDeferredReload();
      if (!this.openVault) return 0;
      const batch = [];
      for (const uuid of Object.keys(changesByUuid)) {
        const row = this.rowFor(uuid);
        const change = changesByUuid[uuid];
        if (!row || changesNothing(row, change)) continue;
        batch.push({ row: row, change: change });
      }
      if (!batch.length) return 0;

      const vault = this.openVault;
      let written = 0;
      let failure = null;
      this.busy = true;
      try {
        batch.forEach(function (item) { window.vaultResign.assertMayResign(item.row); });
        for (const item of batch) {
          const body = await window.buildEntryResignRequest(
            window.vaultSession, vault, item.row, item.change
          );
          await window.vaultApi.updateEntry(item.row.uuid, body);
          written += 1;
        }
      } catch (err) {
        if (err && err.reason === 'locked') return written;
        failure = err;
      } finally {
        this.busy = false;
      }
      // The reload first: it is what makes the listing describe what was
      // written, and it clears the error line on its way in.
      await this.load();
      if (failure && failure.name === 'VaultResignBlocked' && written === 0) {
        this.error = 'An entry could not be verified. Nothing was changed.';
      } else if (failure) {
        this.error = 'Some entries could not be changed. The list above is up to date.';
      }
      return written;
    },

    moveEntries: async function (uuids, folderUuid) {
      const target = folderUuid || null;
      const changes = {};
      uuids.forEach(function (uuid) { changes[uuid] = { folder: target }; });
      const place = target === null
        ? this.vaultName()
        : (this.folders.find(function (folder) {
          return String(folder.uuid) === String(target);
        }) || {}).name || '';
      const written = await this.resignEntries(changes);
      // A lock mid-batch has already cleared the page; a notice set after it
      // would put a decrypted folder name back on a locked screen.
      if (!window.vaultSession.isUnlocked()) return;
      if (!written || this.error) return;
      this.fileNotice = written === 1
        ? 'Moved 1 entry to ' + place + '.'
        : 'Moved ' + written + ' entries to ' + place + '.';
    },

    // Over the vault's tags, how many of the selected rows carry each one.
    tagStates: function (uuids) {
      const rows = uuids.map((uuid) => this.rowFor(uuid)).filter(Boolean);
      const states = {};
      this.tags.forEach(function (tag) {
        const carrying = rows.filter(function (row) {
          return (row.tags || []).some(function (uuid) {
            return String(uuid) === String(tag.uuid);
          });
        }).length;
        if (rows.length && carrying === rows.length) states[tag.uuid] = 'all';
        else if (carrying > 0) states[tag.uuid] = 'some';
        else states[tag.uuid] = 'none';
      });
      return states;
    },

    // 'all' puts the tag on every row, 'none' takes it off every row, and
    // 'some' leaves each row as it is - the dash is the selection's mixed
    // state, not a third value to write. A row left with the tags it had is
    // absent from the map.
    tagChangesFor: function (uuids, states) {
      const changes = {};
      uuids.forEach((uuid) => {
        const row = this.rowFor(uuid);
        if (!row) return;
        let tags = (row.tags || []).slice();
        Object.keys(states).forEach(function (tagUuid) {
          const has = tags.some(function (uuid) { return String(uuid) === String(tagUuid); });
          if (states[tagUuid] === 'all' && !has) tags.push(tagUuid);
          if (states[tagUuid] === 'none' && has) {
            tags = tags.filter(function (uuid) { return String(uuid) !== String(tagUuid); });
          }
        });
        if (!sameTags(row.tags, tags)) changes[uuid] = { tags: tags };
      });
      return changes;
    },
  };
};

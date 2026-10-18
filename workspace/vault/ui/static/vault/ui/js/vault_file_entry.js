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
    // The rows being dragged, and the folder the pointer is over (null is the
    // vault root, undefined is none).
    draggedUuids: [],
    dropTarget: undefined,

    openMoveDialog: function (rows) {
      this.moveDialog = {
        uuids: rows.map(function (row) { return row.uuid; }),
        target: undefined,
      };
    },

    closeMoveDialog: function () {
      this.moveDialog = null;
    },

    // Every folder of the open vault, parents before their children, siblings
    // in the order the listing shows them.
    folderTree: function () {
      const byParent = new Map();
      this.folders.forEach(function (folder) {
        const key = folder.parent || null;
        if (!byParent.has(key)) byParent.set(key, []);
        byParent.get(key).push(folder);
      });
      const tree = [];
      const walk = function (parent, depth) {
        (byParent.get(parent) || []).forEach(function (folder) {
          tree.push({ uuid: folder.uuid, name: folder.name, depth: depth });
          walk(folder.uuid, depth + 1);
        });
      };
      walk(null, 0);
      return tree;
    },

    // The folder every given entry sits in (null for the vault root), or
    // undefined when they sit in different places.
    commonFolder: function (uuids) {
      const places = new Set();
      uuids.forEach((uuid) => {
        const entry = this.entries.find(function (item) { return item.uuid === uuid; });
        places.add(entry && entry.folder ? entry.folder : null);
      });
      return places.size === 1 ? [...places][0] : undefined;
    },

    canConfirmMove: function () {
      const dialog = this.moveDialog;
      if (!dialog || this.busy || dialog.target === undefined) return false;
      return dialog.target !== this.commonFolder(dialog.uuids);
    },

    // The offer can change while the dialog is open (a reload, a trash from
    // another tab), so the registry is asked again before anything is signed.
    confirmMove: async function () {
      if (!this.canConfirmMove()) return;
      const { uuids, target } = this.moveDialog;
      const stillOffered = uuids.every((uuid) => {
        const entry = this.entries.find(function (item) { return item.uuid === uuid; });
        return entry && this.hasAction(entry, 'move');
      });
      if (!stillOffered) {
        this.moveDialog = null;
        return;
      }
      await this.moveEntries(uuids, target);
      this.moveDialog = null;
    },

    openTagsDialog: function (rows) {
      const uuids = rows.map(function (row) { return row.uuid; });
      const start = this.tagStates(uuids);
      this.tagsDialog = { uuids: uuids, start: start, states: Object.assign({}, start) };
    },

    closeTagsDialog: function () {
      this.tagsDialog = null;
    },

    // all -> none -> some -> all. The dash is only a place to come back to:
    // a tag that no mixed selection started on has no dash to offer.
    cycleTag: function (tagUuid) {
      const dialog = this.tagsDialog;
      if (!dialog) return;
      const state = dialog.states[tagUuid];
      if (state === 'all') dialog.states[tagUuid] = 'none';
      else if (state === 'none') {
        dialog.states[tagUuid] = dialog.start[tagUuid] === 'some' ? 'some' : 'all';
      } else dialog.states[tagUuid] = 'all';
    },

    // How many of the dialog's entries carry the tag right now.
    dialogTagCount: function (tagUuid) {
      if (!this.tagsDialog) return 0;
      return this.tagsDialog.uuids.filter((uuid) => {
        const row = this.rowFor(uuid);
        return row && (row.tags || []).some(function (carried) {
          return String(carried) === String(tagUuid);
        });
      }).length;
    },

    // What Apply would do, one line per tag that moved off its starting
    // state. The count is the entries the line really changes: a tag added to
    // a selection that half has it touches only the other half.
    tagSummary: function () {
      const dialog = this.tagsDialog;
      if (!dialog) return [];
      const total = dialog.uuids.length;
      const lines = [];
      this.tags.forEach((tag) => {
        const state = dialog.states[tag.uuid];
        if (state === dialog.start[tag.uuid] || state === 'some') return;
        const changing = state === 'all' ? total - this.dialogTagCount(tag.uuid) : this.dialogTagCount(tag.uuid);
        const suffix = total > 1
          ? ' (' + changing + (changing === 1 ? ' entry' : ' entries') + ')'
          : '';
        lines.push((state === 'all' ? 'Adds ' : 'Removes ') + tag.name + suffix);
      });
      return lines;
    },

    canConfirmTags: function () {
      return Boolean(this.tagsDialog) && !this.busy && this.tagSummary().length > 0;
    },

    // The offer can change while the dialog is open, so the registry is asked
    // again before anything is signed.
    confirmTags: async function () {
      if (!this.canConfirmTags()) return;
      const { uuids, states } = this.tagsDialog;
      const stillOffered = uuids.every((uuid) => {
        const entry = this.entries.find(function (item) { return item.uuid === uuid; });
        return entry && this.hasAction(entry, 'set_tags');
      });
      if (!stillOffered) {
        this.tagsDialog = null;
        return;
      }
      await this.resignEntries(this.tagChangesFor(uuids, states));
      this.tagsDialog = null;
    },

    // Only where the rows sit in the folder the listing shows: in Favorites, a
    // tag view or the trash a drop would send them out of their folder.
    canDragEntries: function () {
      return this.view === 'all' && !this.tagFilter;
    },

    // Dragging a selected row takes the whole selection; any other row goes
    // alone.
    onEntryDragStart: function (event, entry) {
      if (!this.canDragEntries()) {
        event.preventDefault();
        return;
      }
      this.draggedUuids = this.isSelected(entry.uuid) ? this.selected.slice() : [entry.uuid];
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('application/x-vault-entries', this.draggedUuids.join(','));
      if (this.draggedUuids.length > 1) {
        const ghost = document.getElementById('drag-ghost');
        if (ghost) {
          ghost.textContent = this.draggedUuids.length + ' entries';
          event.dataTransfer.setDragImage(ghost, 12, 12);
        }
      }
    },

    // The folder the listing shows is where the rows already are, so it is no
    // target.
    isDropFolder: function (folderUuid) {
      return (folderUuid || null) !== (this.folderUuid || null);
    },

    onFolderDragOver: function (event, folderUuid) {
      if (!this.draggedUuids.length || !this.isDropFolder(folderUuid)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      this.dropTarget = folderUuid || null;
    },

    // Crossing from a row into one of its own cells also fires a leave.
    onFolderDragLeave: function (event) {
      const next = event.relatedTarget;
      if (next && event.currentTarget && event.currentTarget.contains(next)) return;
      this.dropTarget = undefined;
    },

    // The registry is asked again, as the dialog does: a row it no longer
    // offers a move for stops the whole drop.
    onFolderDrop: async function (event, folderUuid) {
      event.preventDefault();
      const uuids = this.draggedUuids;
      this.onEntryDragEnd();
      if (!uuids.length || this.busy || !this.isDropFolder(folderUuid)) return;
      const movable = uuids.every((uuid) => {
        const entry = this.entries.find(function (item) { return item.uuid === uuid; });
        return entry && this.hasAction(entry, 'move');
      });
      if (!movable) return;
      await this.moveEntries(uuids, folderUuid || null);
    },

    onEntryDragEnd: function () {
      this.draggedUuids = [];
      this.dropTarget = undefined;
    },

    clearFileEntry: function () {
      this.draggedUuids = [];
      this.dropTarget = undefined;
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

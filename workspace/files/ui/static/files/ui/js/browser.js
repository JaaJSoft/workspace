window.fileBrowser = function fileBrowser() {
  const tags = window.tagsMixin();

  return {
    // Tag CRUD + assignment (tag_dropdown.html / tag_dialog.html bindings).
    // The mixin reads/writes `selectedFile`, seeded by the properties partial.
    ...tags,

    // Assignments change the chips rendered in the listing, so announce
    // them the same way the mixin announces tag create/edit/delete.
    async toggleFileTag(tag) {
      await tags.toggleFileTag.call(this, tag);
      window.dispatchEvent(new CustomEvent('tags-changed'));
    },

    cleaningTrash: false,

    // True from the moment a folder request leaves until the new listing is
    // bound. The binding of a large folder blocks the page for a while, and
    // that wait is part of what the user is looking at the overlay for, so
    // the flag only clears once the new #folder-browser announces itself.
    folderLoading: false,

    // Chains batch intakes: two batches deciding name collisions at once
    // would stack their dialogs.
    _uploadIntake: Promise.resolve(),

    // An item an action runs on is dimmed behind a spinner until the action
    // settles: data-busy, styled in folder_content.html.
    _startLoading(...uuids) {
      this._paintBusy(uuids, true);
    },

    _stopLoading(...uuids) {
      this._paintBusy(uuids, false);
    },

    _paintBusy(uuids, busy) {
      for (const uuid of uuids) {
        document.querySelector(`#folder-browser [data-uuid="${CSS.escape(uuid)}"]`)
          ?.toggleAttribute('data-busy', busy);
      }
    },

    // Properties panel state and loading (properties_panel.js). Its
    // `selectedFile` is also the tags mixin's target.
    ...window.propertiesPanelMixin(),

    get currentFolder() {
      const folderEl = document.getElementById('folder-browser');
      return folderEl?.dataset.folder || '';
    },

    _initFileActions() {
      // Listen for file actions from context menu
      window.addEventListener('file-action', (e) => {
        const { action, uuid, name, nodeType, state } = e.detail;

        switch (action) {
          case 'toggle_favorite':
            this.toggleFavorite(uuid, state && state.is_favorite);
            break;
          case 'toggle_pin':
            this.togglePin(uuid, state && state.is_pinned);
            break;
          case 'toggle_sensitive':
            this.markSensitive([uuid], !(state && state.is_sensitive));
            break;
          case 'rename':
            this.showRenameDialog(uuid, name);
            break;
          case 'delete':
            this.confirmDelete(uuid, name, nodeType, !!e.detail.isGroupFolder);
            break;
          case 'restore':
            this.confirmRestore(uuid, name, nodeType);
            break;
          case 'purge':
            this.confirmPurge(uuid, name, nodeType);
            break;
          case 'cut':
            this.cutToClipboard([{ uuid, name, nodeType }]);
            break;
          case 'copy':
            this.copyToClipboard([{ uuid, name, nodeType }]);
            break;
          case 'paste_into':
            this.pasteFromClipboard();
            break;
          case 'extract':
            this.extractArchive(uuid, name);
            break;
          case 'manage_tags':
            this.openTagPicker(uuid, name);
            break;
          case 'create_shortcut':
            this.createShortcut(uuid, name);
            break;
        }
      });

      // A drop on a folder, from the listing, the breadcrumbs or the sidebar
      window.addEventListener('file-move-request', (e) => {
        this.moveItemsTo(e.detail.items, e.detail.targetFolderId);
      });

      window.addEventListener('uploads-changed', () => {
        this.refreshFolderBrowser();
      });

      // Listen for folder icon changes (from properties panel)
      window.addEventListener('folder-icons-changed', () => {
        this.refreshFolderBrowser();
      });

      // Listen for folder background actions (from background context menu)
      window.addEventListener('folder-action', (e) => {
        const { action } = e.detail;
        switch (action) {
          case 'createFolder':
            this.showCreateFolderDialog();
            break;
          case 'createFile':
            this.showCreateFileDialog();
            break;
          case 'upload':
            this.triggerUpload();
            break;
          case 'paste':
            this.pasteFromClipboard();
            break;
        }
      });

      // Listen for bulk actions
      window.addEventListener('bulk-action', (e) => {
        const { action, uuids, add } = e.detail;
        switch (action) {
          case 'delete':
            this.bulkDeleteItems(uuids);
            break;
          case 'favorite':
            this.bulkToggleFavorite(uuids, add);
            break;
          case 'cut':
            this.bulkCutToClipboard(uuids);
            break;
          case 'copy':
            this.bulkCopyToClipboard(uuids);
            break;
          case 'paste':
            this.pasteFromClipboard();
            break;
          case 'restore':
            this.bulkRestoreItems(uuids);
            break;
          case 'purge':
            this.bulkPurgeItems(uuids);
            break;
          case 'pin':
            this.bulkTogglePin(uuids, add);
            break;
          case 'sensitive':
            this.markSensitive(uuids, add);
            break;
          case 'download':
            this.bulkDownload(uuids);
            break;
        }
      });
    },

    showCreateFolderDialog() {
      window.fileActions.showCreateFolderDialog();
    },

    showCreateFileDialog(defaultType = 'txt') {
      const dialog = document.getElementById('create-file-dialog');
      if (!dialog) return;
      const nameInput = dialog.querySelector('[x-ref="fileName"]');
      const typeSelect = dialog.querySelector('select');
      const customInput = dialog.querySelector('[x-ref="customExt"]');
      if (nameInput) {
        nameInput.value = '';
      }
      if (customInput) {
        customInput.value = '';
      }
      if (typeSelect) {
        typeSelect.value = defaultType;
        typeSelect.dispatchEvent(new Event('change', { bubbles: true }));
      }
      dialog.showModal();
      setTimeout(() => nameInput && nameInput.focus(), 100);
    },

    showRenameDialog(uuid, name) {
      window.fileActions.showRenameDialog(uuid, name);
    },

    async confirmDelete(uuid, name, nodeType, isGroupFolder) {
      if (window.getFilePrefs().confirmBeforeDelete) {
        const confirmed = await AppDialog.confirm({
          title: nodeType === 'folder' ? gettext('Delete folder?') : gettext('Delete file?'),
          message: nodeType === 'folder'
            ? interpolate(gettext('Move "%(name)s" to trash? This will also move all contents.'), { name }, true)
            : interpolate(gettext('Move "%(name)s" to trash?'), { name }, true),
          okLabel: gettext('Move to trash'),
          okClass: 'btn-error',
          icon: 'trash-2',
          iconClass: 'bg-error/10 text-error'
        });
        if (!confirmed) return;
      }
      await this.deleteItem(uuid);
      if (isGroupFolder) {
        window.dispatchEvent(new CustomEvent('group-folders-changed'));
      }
    },

    async createFolder(name) {
      try {
        await window.fileActions.createFolder(name, this.currentFolder || null);
        this.refreshFolderBrowser();
      } catch (error) {
        window.AppAlert.error(error.message || gettext('Failed to create folder'));
      }
    },


    async createFile(name, fileType, customExt) {
      const trimmedName = (name || '').trim();
      if (!trimmedName) {
        window.AppAlert.error(gettext('File name is required'));
        return;
      }

      const typeMap = {
        txt: { ext: 'txt', mime: 'text/plain' },
        md: { ext: 'md', mime: 'text/markdown' },
        json: { ext: 'json', mime: 'application/json' },
        csv: { ext: 'csv', mime: 'text/csv' }
      };

      let extension = '';
      let mimeType = '';
      if (fileType === 'custom') {
        extension = (customExt || '').trim().replace(/^\./, '');
        if (!extension) {
          window.AppAlert.error(gettext('Custom extension is required'));
          return;
        }
        mimeType = 'application/octet-stream';
      } else if (typeMap[fileType]) {
        extension = typeMap[fileType].ext;
        mimeType = typeMap[fileType].mime;
      }

      let finalName = trimmedName;
      const lowerName = trimmedName.toLowerCase();
      if (extension) {
        const extSuffix = `.${extension.toLowerCase()}`;
        if (!lowerName.endsWith(extSuffix) && !trimmedName.includes('.')) {
          finalName = `${trimmedName}.${extension}`;
        } else if (fileType === 'custom' && !lowerName.endsWith(extSuffix)) {
          finalName = `${trimmedName}.${extension}`;
        }
      }

      const file = new File([''], finalName, {
        type: mimeType || 'application/octet-stream'
      });
      const formData = new FormData();
      formData.append('name', finalName);
      formData.append('node_type', 'file');
      formData.append('content', file);
      if (mimeType) {
        formData.append('mime_type', mimeType);
      }
      if (this.currentFolder) {
        formData.append('parent', this.currentFolder);
      }

      try {
        const response = await fetch('/api/v1/files', {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken()
          },
          body: formData
        });
        if (response.ok) {
          document.getElementById('create-file-dialog').close();
          this.refreshFolderBrowser();
        } else {
          let data = {};
          try {
            data = await response.json();
          } catch (error) {
            data = {};
          }
          window.AppAlert.error(data.detail || gettext('Failed to create file'));
        }
      } catch (error) {
        window.AppAlert.error(gettext('Failed to create file'));
      }
    },

    triggerUpload() {
      const input = document.getElementById('file-upload-input');
      if (input) {
        input.click();
      }
    },

    async handleUpload(event) {
      const files = Array.from(event.target.files);
      // Cleared at once so picking the same files again still fires change.
      event.target.value = '';
      if (!files.length) return;
      await this.uploadFiles(files);
    },

    // The target folder is read once, here: the queue keeps it per file, so
    // navigating while it drains never redirects the rest of the batch.
    uploadFiles(files) {
      const batch = Array.from(files);
      const folderId = this.currentFolder || null;
      const intake = async () => {
        const decisions = await this._decideNameCollisions(batch, { targetFolderId: folderId });
        Alpine.store('uploads').add(
          batch.map((file) => ({ file, folderId, onConflict: decisions.get(file) }))
        );
      };
      this._uploadIntake = this._uploadIntake.catch(() => {}).then(intake);
      return this._uploadIntake;
    },

    // Map each file to what the server should do if its name is taken
    // ('replace' | 'rename' | 'skip' | undefined = let the server reject).
    // Only the "ask" and "skip" preferences need to know about a collision
    // before the bytes go up; the other two are decided server-side.
    async _decideNameCollisions(files, { replaceLabel = gettext('Replace the existing file'), targetFolderId } = {}) {
      const mode = window.getFilePrefs().nameCollision || 'ask';
      const decisions = new Map();
      if (mode === 'replace' || mode === 'keep_both') {
        for (const file of files) decisions.set(file, mode === 'replace' ? 'replace' : 'rename');
        return decisions;
      }
      const taken = await this._siblingFileNames(
        targetFolderId === undefined ? this.currentFolder || null : targetFolderId
      );
      const colliding = files.filter(file => taken.has(file.name.toLowerCase()));
      let chosenForRest = mode === 'skip' ? 'skip' : null;
      for (const [index, file] of colliding.entries()) {
        if (chosenForRest) {
          decisions.set(file, chosenForRest);
          continue;
        }
        const remaining = colliding.length - index - 1;
        const answer = await AppDialog.select({
          title: gettext('File already exists'),
          message: interpolate(
            gettext('A file named "%(name)s" already exists in this folder. What should happen to the new one?'),
            { name: file.name },
            true
          ),
          options: [
            { value: 'rename', label: gettext('Keep both (rename the new file)') },
            { value: 'replace', label: replaceLabel },
            { value: 'skip', label: gettext('Skip this file') },
          ],
          value: 'rename',
          okLabel: gettext('Continue'),
          icon: 'files',
          iconClass: 'bg-module/15 text-module',
          checkbox: remaining
            ? interpolate(
              ngettext(
                'Do the same for the %(count)s other conflict',
                'Do the same for the %(count)s other conflicts',
                remaining
              ),
              { count: remaining },
              true
            )
            : '',
        });
        const { value, checked } = remaining ? answer : { value: answer, checked: false };
        // Closing the dialog is the safe choice: the existing file stays.
        const choice = value || 'skip';
        decisions.set(file, choice);
        if (checked) chosenForRest = choice;
      }
      return decisions;
    },

    async _siblingFileNames(folderId) {
      const params = new URLSearchParams({ node_type: 'file' });
      if (folderId) params.set('parent', folderId);
      try {
        const response = await fetch(`/api/v1/files?${params}`, { headers: { Accept: 'application/json' } });
        if (!response.ok) return new Set();
        const rows = await response.json();
        return new Set((Array.isArray(rows) ? rows : rows.results || []).map(f => f.name.toLowerCase()));
      } catch (_) {
        return new Set();
      }
    },

    _pasteSummary(isCopy, { done, replaced, renamed, skipped, failed }) {
      const parts = [];
      if (done) {
        parts.push(interpolate(
          isCopy
            ? ngettext('Copied %(count)s item', 'Copied %(count)s items', done)
            : ngettext('Moved %(count)s item', 'Moved %(count)s items', done),
          { count: done },
          true
        ));
      }
      if (replaced) parts.push(interpolate(ngettext('replaced %(count)s', 'replaced %(count)s', replaced), { count: replaced }, true));
      if (renamed) {
        parts.push(interpolate(
          ngettext('kept %(count)s under a new name', 'kept %(count)s under a new name', renamed),
          { count: renamed },
          true
        ));
      }
      if (skipped) parts.push(interpolate(ngettext('skipped %(count)s', 'skipped %(count)s', skipped), { count: skipped }, true));
      if (failed) parts.push(interpolate(ngettext('%(count)s failed', '%(count)s failed', failed), { count: failed }, true));
      if (!parts.length) return '';
      const text = parts.join(', ');
      return text.charAt(0).toUpperCase() + text.slice(1);
    },

    // --- Drag & drop upload ---
    dropZoneActive: false,
    _dropCounter: 0,

    get canUpload() {
      return !!document.getElementById('file-upload-input');
    },

    onFileDragEnter(e) {
      if (!e.dataTransfer.types.includes('Files') || !this.canUpload) return;
      e.preventDefault();
      this._dropCounter++;
      this.dropZoneActive = true;
    },

    onFileDragOver(e) {
      if (!e.dataTransfer.types.includes('Files') || !this.canUpload) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    },

    onFileDragLeave() {
      this._dropCounter--;
      if (this._dropCounter <= 0) {
        this._dropCounter = 0;
        this.dropZoneActive = false;
      }
    },

    async onFileDrop(e) {
      e.preventDefault();
      this._dropCounter = 0;
      this.dropZoneActive = false;
      const files = e.dataTransfer.files;
      if (!files.length || !this.canUpload) return;
      await this.uploadFiles(files);
    },

    async renameItem(uuid, newName) {
      this._startLoading(uuid);
      try {
        await window.fileActions.renameItem(uuid, newName);
        window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
        this.refreshFolderBrowser();
      } catch (error) {
        window.AppAlert.error(error.message || gettext('Failed to rename'));
      } finally {
        this._stopLoading(uuid);
      }
    },

    deleteItem(uuid) {
      return this._runOperation('trash', [uuid]);
    },


    async confirmRestore(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: nodeType === 'folder' ? gettext('Restore folder?') : gettext('Restore file?'),
        message: interpolate(gettext('Restore "%(name)s" from trash?'), { name }, true),
        okLabel: gettext('Restore'),
        okClass: 'btn-module',
        icon: 'rotate-ccw',
        iconClass: 'bg-module/15 text-module'
      });
      if (confirmed) {
        this.restoreItem(uuid);
      }
    },

    restoreItem(uuid) {
      return this._runOperation('restore', [uuid]);
    },


    async confirmPurge(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: nodeType === 'folder' ? gettext('Delete folder permanently?') : gettext('Delete file permanently?'),
        message: interpolate(gettext('This will permanently delete "%(name)s" and cannot be undone.'), { name }, true),
        okLabel: gettext('Delete permanently'),
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error'
      });
      if (confirmed) {
        this.purgeItem(uuid);
      }
    },

    purgeItem(uuid) {
      return this._runOperation('purge', [uuid]);
    },


    async confirmCleanTrash() {
      const confirmed = await AppDialog.confirm({
        title: gettext('Empty trash?'),
        message: gettext('This will permanently delete all items in trash and cannot be undone.'),
        okLabel: gettext('Empty trash'),
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error'
      });
      if (confirmed) {
        this.emptyTrash();
      }
    },

    async emptyTrash() {
      this.cleaningTrash = true;
      try {
        await this._runOperation('empty_trash', []);
      } finally {
        this.cleaningTrash = false;
      }
    },

    // Runs a batch operation on *uuids*, greying their rows while it goes,
    // and tells how it ended. *items* replaces the bare uuids when each one
    // carries a conflict choice; *summary* words a success worth announcing.
    // The listing refreshes on `file-operation-finished`, wherever the
    // operation was started. Returns the report, or null when refused.
    async _runOperation(kind, uuids, { target, items, summary } = {}) {
      this._startLoading(...uuids);
      try {
        const options = target === undefined ? {} : { target };
        const report = await window.fileOperations.run(kind, items || uuids, options);
        window.fileOperations.announce(report, { summary });
        return report;
      } catch (error) {
        window.AppAlert.error(error.message || gettext('Something went wrong'));
        return null;
      } finally {
        this._stopLoading(...uuids);
      }
    },


    async toggleFavorite(uuid, isFavorite) {
      if (!uuid) return;
      await this._mark(window.fileActions.setFavorite, [uuid], !isFavorite, gettext('Failed to update favorites'));
    },


    // Blurs the previews for everyone who can open the files, or stops
    // blurring them.
    async markSensitive(uuids, sensitive) {
      if (!uuids.length) return;
      uuids.forEach(uuid => this._startLoading(uuid));
      try {
        const response = await fetch('/api/v1/files/sensitive', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken()
          },
          body: JSON.stringify({ uuids, sensitive })
        });
        if (response.ok) {
          this.refreshFolderBrowser();
          return;
        }
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = {};
        }
        window.AppAlert.error(data.detail || gettext('Failed to update the files'));
      } catch (error) {
        window.AppAlert.error(gettext('Failed to update the files'));
      } finally {
        uuids.forEach(uuid => this._stopLoading(uuid));
      }
    },

    async togglePin(uuid, isPinned) {
      if (!uuid) return;
      await this._mark(window.fileActions.setPinned, [uuid], !isPinned, gettext('Failed to update pin'));
    },


    // Bulk actions
    async bulkDeleteItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      if (window.getFilePrefs().confirmBeforeDelete) {
        const count = uuids.length;
        const confirmed = await AppDialog.confirm({
          title: interpolate(ngettext('Delete %(count)s item?', 'Delete %(count)s items?', count), { count }, true),
          message: interpolate(
            ngettext(
              'Move %(count)s item to trash? Folders will also move all their contents.',
              'Move %(count)s items to trash? Folders will also move all their contents.',
              count
            ),
            { count },
            true
          ),
          okLabel: gettext('Move to trash'),
          okClass: 'btn-error',
          icon: 'trash-2',
          iconClass: 'bg-error/10 text-error'
        });
        if (!confirmed) return;
      }

      await this._runOperation('trash', uuids, {
        summary: (report) => interpolate(
          ngettext('Deleted %(count)s item', 'Deleted %(count)s items', report.counts.done || 0),
          { count: report.counts.done || 0 },
          true
        ),
      });
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    async bulkToggleFavorite(uuids, add) {
      if (!uuids || uuids.length === 0) return;
      const done = await this._mark(window.fileActions.setFavorite, uuids, add, gettext('Failed to update favorites'));
      if (done) {
        const success = done.length;
        this._announceMarks(success, uuids.length - success, add
          ? { done: ngettext('Added to favorites: %(count)s item', 'Added to favorites: %(count)s items', success),
              partial: gettext('Added to favorites: %(success)s succeeded, %(failed)s failed') }
          : { done: ngettext('Removed from favorites: %(count)s item', 'Removed from favorites: %(count)s items', success),
              partial: gettext('Removed from favorites: %(success)s succeeded, %(failed)s failed') });
      }
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    // Mark or unmark *uuids* in one request, greying their rows meanwhile.
    // Resolves to the uuids now as asked, or null when the request failed.
    async _mark(setter, uuids, value, failure) {
      this._startLoading(...uuids);
      try {
        const done = await setter(uuids, value);
        window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
        this.refreshFolderBrowser();
        return done;
      } catch (error) {
        window.AppAlert.error(error.message || failure);
        return null;
      } finally {
        this._stopLoading(...uuids);
      }
    },

    _announceMarks(success, failed, { done, partial }) {
      if (failed) {
        window.AppAlert.warning(interpolate(partial, { success, failed }, true));
      } else {
        window.AppAlert.success(interpolate(done, { count: success }, true));
      }
    },


    async bulkRestoreItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      const count = uuids.length;
      const confirmed = await AppDialog.confirm({
        title: gettext('Restore items?'),
        message: interpolate(ngettext('Restore %(count)s item from trash?', 'Restore %(count)s items from trash?', count), { count }, true),
        okLabel: gettext('Restore'),
        okClass: 'btn-module',
        icon: 'rotate-ccw',
        iconClass: 'bg-module/15 text-module'
      });
      if (!confirmed) return;

      await this._runOperation('restore', uuids, {
        summary: (report) => interpolate(
          ngettext('Restored %(count)s item', 'Restored %(count)s items', report.counts.done || 0),
          { count: report.counts.done || 0 },
          true
        ),
      });
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    async bulkPurgeItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      const count = uuids.length;
      const confirmed = await AppDialog.confirm({
        title: gettext('Delete permanently?'),
        message: interpolate(
          ngettext(
            'This will permanently delete %(count)s item and cannot be undone.',
            'This will permanently delete %(count)s items and cannot be undone.',
            count
          ),
          { count },
          true
        ),
        okLabel: gettext('Delete permanently'),
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error'
      });
      if (!confirmed) return;

      await this._runOperation('purge', uuids, {
        summary: (report) => interpolate(
          ngettext(
            'Permanently deleted %(count)s item',
            'Permanently deleted %(count)s items',
            report.counts.done || 0
          ),
          { count: report.counts.done || 0 },
          true
        ),
      });
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    bulkDownload(uuids) {
      return window.fileActions.downloadArchive(uuids);
    },

    async bulkTogglePin(uuids, add) {
      if (!uuids || uuids.length === 0) return;
      const done = await this._mark(window.fileActions.setPinned, uuids, add, gettext('Failed to update pin'));
      if (done) {
        const success = done.length;
        this._announceMarks(success, uuids.length - success, add
          ? { done: ngettext('Pinned %(count)s item', 'Pinned %(count)s items', success),
              partial: gettext('Pinned %(success)s items, %(failed)s failed') }
          : { done: ngettext('Unpinned %(count)s item', 'Unpinned %(count)s items', success),
              partial: gettext('Unpinned %(success)s items, %(failed)s failed') });
      }
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },


    // Clipboard operations
    cutToClipboard(items) {
      if (!items || items.length === 0) return;
      window.fileClipboard.cut(this._tagSourceFolder(items));
      const count = items.length;
      window.AppAlert.info(interpolate(
        ngettext('%(count)s item cut to clipboard', '%(count)s items cut to clipboard', count),
        { count },
        true
      ));
    },

    copyToClipboard(items) {
      if (!items || items.length === 0) return;
      window.fileClipboard.copy(this._tagSourceFolder(items));
      const count = items.length;
      window.AppAlert.info(interpolate(
        ngettext('%(count)s item copied to clipboard', '%(count)s items copied to clipboard', count),
        { count },
        true
      ));
    },

    bulkCutToClipboard(uuids) {
      if (!uuids || uuids.length === 0) return;
      const items = this._getItemsFromUuids(uuids);
      this.cutToClipboard(items);
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    bulkCopyToClipboard(uuids) {
      if (!uuids || uuids.length === 0) return;
      const items = this._getItemsFromUuids(uuids);
      this.copyToClipboard(items);
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    // Paste needs to know where an item came from: pasting a copy back into
    // its own folder is a plain duplicate ("name (Copy).ext"), not a
    // collision worth a dialog.
    _tagSourceFolder(items) {
      const sourceFolder = this.currentFolder || null;
      return items.map(item => ({ ...item, sourceFolder }));
    },

    _getItemsFromUuids(uuids) {
      return uuids.map(uuid => {
        const row = document.querySelector(`[data-uuid="${uuid}"]`);
        return {
          uuid,
          name: row?.dataset.displayName || '',
          nodeType: row?.dataset.nodeType || 'file'
        };
      });
    },

    async pasteFromClipboard() {
      const items = window.fileClipboard.getItems();
      if (!items || items.length === 0) {
        window.AppAlert.warning(gettext('Clipboard is empty'));
        return;
      }

      const isCopy = window.fileClipboard.isCopy();
      await this._transferItems(items, this.currentFolder || null, { isCopy });

      // Only clear clipboard on cut (move), keep it for copy
      if (!isCopy) {
        window.fileClipboard.clear();
      }
    },

    // A drop on a folder (listing, breadcrumb or sidebar entry) - see
    // drag_move.js for the gesture, this is where the move happens.
    async moveItemsTo(items, targetFolderId) {
      if (!items || items.length === 0) return;
      await this._transferItems(items, targetFolderId, { isCopy: false });
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    // Moves (or copies) every item into targetFolderId, resolving name
    // collisions the way the user's preference says, and announces the
    // outcome. Items carry {uuid, name, nodeType} and optionally the
    // sourceFolder they were taken from.
    async _transferItems(items, targetFolderId, { isCopy }) {
      // Only a file landing in another folder can collide with a sibling:
      // folders are never unique-checked, and a copy into its own folder is
      // just a duplicate the server suffixes on its own. An item whose origin
      // is unknown (dragged from favorites, recent, a tag) is checked too.
      const collidable = items.filter(
        item => item.nodeType === 'file'
          && (item.sourceFolder === undefined || (item.sourceFolder || null) !== targetFolderId)
      );
      const decisions = await this._decideNameCollisions(collidable, {
        replaceLabel: isCopy
          ? gettext('Replace the existing file')
          : gettext('Replace the existing file (the moved one goes to the trash)'),
        targetFolderId,
      });

      // A collision decided as 'skip' is the server's to skip, so it counts
      // with the rest. An item with no collision at pre-check time still
      // refuses one that appears before its turn, rather than let the copy
      // default rename it silently.
      const payload = items.map((item) => {
        const onConflict = decisions.get(item) || (collidable.includes(item) ? 'error' : null);
        return onConflict ? { uuid: item.uuid, on_conflict: onConflict } : { uuid: item.uuid };
      });
      await this._runOperation(isCopy ? 'copy' : 'move', items.map((item) => item.uuid), {
        target: targetFolderId,
        items: payload,
        summary: (report) => this._pasteSummary(isCopy, report.counts),
      });
    },

    async createShortcut(uuid, name) {
      const folder = await AppDialog.folderPicker({
        title: gettext('Create shortcut'),
        message: interpolate(gettext('Choose where to put a shortcut to "%(name)s".'), { name }, true),
        okLabel: gettext('Create'),
        okClass: 'btn-module',
        icon: 'file-symlink',
        iconClass: 'bg-module/15 text-module',
      });
      if (!folder) return;

      try {
        const resp = await fetch('/api/v1/files', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          // A name already taken there gets the next free "name (Copy)".
          body: JSON.stringify({
            name,
            node_type: 'shortcut',
            shortcut_target: uuid,
            parent: folder.uuid || null,
            on_conflict: 'rename',
          }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          window.AppAlert.error(window.fileActions.firstErrorMessage(body));
          return;
        }
        window.AppAlert.success(interpolate(
          gettext('Shortcut created in "%(folder)s"'), { folder: folder.name }, true
        ));
        // Root is null on both sides: the picker's root and an empty dataset.
        if ((folder.uuid || null) === (this.currentFolder || null)) {
          this.refreshFolderBrowser();
        }
      } catch (e) {
        window.AppAlert.error(gettext('Failed to create shortcut'));
      }
    },

    async extractArchive(uuid, name) {
      const folder = await AppDialog.folderPicker({
        title: gettext('Extract archive'),
        message: interpolate(gettext('Choose where to extract "%(name)s".'), { name }, true),
        okLabel: gettext('Extract'),
        okClass: 'btn-module',
        icon: 'archive-restore',
        iconClass: 'bg-module/15 text-module',
      });
      if (!folder) return;

      await this._runOperation('extract', [uuid], {
        target: folder.uuid || null,
        summary: () => interpolate(gettext('"%(name)s" extracted'), { name }, true),
      });
    },

    async pinFolder(uuid) {
      if (!uuid) return;
      await this._mark(window.fileActions.setPinned, [uuid], true, gettext('Failed to pin folder'));
    },


    async syncAndRefreshFolderBrowser() {
      try {
        const folderId = this.currentFolder;
        const syncUrl = folderId
          ? `/api/v1/files/${folderId}/sync`
          : '/api/v1/files/sync';

        await fetch(syncUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken(),
          },
        });
      } catch (error) {
        console.warn('Folder sync failed:', error);
      }

      this.refreshFolderBrowser();
      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
    },

    refreshFolderBrowser() {
      this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
    },

    // --- Tag navigation (the contract the shared tag partials call) ---
    tagViewHref(tag) {
      return '/files?tag=' + tag.uuid;
    },

    openTagView(tag) {
      window.folderNav.navigateTo(this.tagViewHref(tag));
    },

    isTagViewActive(tag) {
      return this.activeView === 'tag:' + tag.uuid;
    },

    // --- Tag picker (context menu "Tags" action) ---
    // The tags mixin operates on `selectedFile`, which the properties panel
    // also owns. The picker borrows it while open and hands it back on close.
    tagPickerName: '',
    _tagPickerOpen: false,
    _tagPickerRestore: null,

    async openTagPicker(uuid, name) {
      this.tagPickerName = name || '';
      this._tagPickerRestore = this.selectedFile;

      // Same file as the open properties panel: reuse its live object so the
      // chips there stay in sync with what the picker toggles.
      if (!this.selectedFile || this.selectedFile.uuid !== uuid) {
        const resp = await fetch(`/api/v1/files/${uuid}`);
        if (!resp.ok) {
          window.AppAlert.error(gettext('Failed to load tags'));
          return;
        }
        const data = await resp.json();
        this.selectedFile = { uuid, tags: data.tags || [] };
      }

      this._tagPickerOpen = true;
      document.getElementById('tag-picker-dialog')?.showModal();
    },

    closeTagPicker() {
      document.getElementById('tag-picker-dialog')?.close();
      this.onTagPickerClosed();
    },

    // Hands `selectedFile` back to whoever owned it before the picker.
    // Every dismissal path (Done, backdrop, Escape) routes here, so it
    // must stay idempotent — the guard is what makes it so.
    onTagPickerClosed() {
      if (!this._tagPickerOpen) return;
      this._tagPickerOpen = false;
      this.selectedFile = this._tagPickerRestore;
      this._tagPickerRestore = null;
      this.tagPickerName = '';
    },


    init() {
      this._initFileActions();
      this.loadTags();

      // Catch up on changes made elsewhere while the stream was down
      // (resumed tab, or a bfcache restore after a mobile back). The listing
      // is server-rendered, so re-render it rather than patching state.
      window.addEventListener('sse:reconnect', () => {
        this.loadTags();
        this.refreshFolderBrowser();
      });

      // A file still being scanned and previewed shows a spinner rendered
      // server-side; re-render once its pipeline settles so the spinner goes
      // and the fresh thumbnail appears. Debounced: a batch upload settles
      // one file after another.
      window.addEventListener('sse:files.processing_settled', (e) => {
        const uuid = e.detail && e.detail.file_uuid;
        if (!uuid) return;
        if (this.propertiesUuid === uuid) this.reloadPropertiesPanel();
        if (!document.querySelector(`#folder-browser [data-uuid="${CSS.escape(uuid)}"]`)) return;
        clearTimeout(this._processingRefreshTimer);
        this._processingRefreshTimer = setTimeout(() => this.refreshFolderBrowser(), 400);
      });

      // Whoever started it - this page, another tab's panel, a page reload -
      // what an operation changed shows once it is over.
      window.addEventListener('file-operation-finished', () => {
        window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
        this.refreshFolderBrowser();
      });

      // Listen for form submissions from dialogs
      window.addEventListener('create-folder', (e) => this.createFolder(e.detail.name));
      window.addEventListener('create-file', (e) => this.createFile(e.detail.name, e.detail.fileType, e.detail.customExt));
      window.addEventListener('rename-item', (e) => this.renameItem(e.detail.uuid, e.detail.name));
      window.addEventListener('create-group-folder', (e) => {
        window.fileActions.createGroupFolder(e.detail.groupId, e.detail.groupName)
          .then(() => window.dispatchEvent(new CustomEvent('group-folders-changed')))
          .catch((err) => window.AppAlert.error(err.message || gettext('Failed to create group folder')));
      });

      // The sidebar's Tags section lives outside this component and asks
      // for the dialogs it cannot open itself.
      window.addEventListener('open-tag-manager', () => this.openTagManager());
      window.addEventListener('open-tag-dialog', () => this.showTagModal());

      // Tag chips are rendered server-side in the listing, so any tag edit
      // or assignment needs a re-render. Debounced: ticking several
      // checkboxes in a row should cost one swap, not one per click.
      window.addEventListener('tags-changed', () => {
        clearTimeout(this._tagsRefreshTimer);
        this._tagsRefreshTimer = setTimeout(() => {
          const viewedTag = new URLSearchParams(window.location.search).get('tag');
          // Deleting the tag we're looking at leaves a view that now 404s.
          if (viewedTag && !this.allTags.some((t) => t.uuid === viewedTag)) {
            const link = document.getElementById('folder-nav-replace');
            if (link) {
              link.href = '/files';
              link.click();
              return;
            }
          }
          this.refreshFolderBrowser();
        }, 400);
      });

      // Properties panel events
      window.addEventListener('open-properties', (e) => {
        const { uuid, nodeType } = e.detail;
        this.openPropertiesPanel(uuid, nodeType);
      });
      window.addEventListener('viewer-file-shown', (e) => this.followViewer(e.detail));
      window.addEventListener('nav:restore', () => this.followPropertiesUrl());
      this.followPropertiesUrl();
      // Starred from the viewer: the listing's star column and the panel follow.
      window.addEventListener('file-favorite-changed', (e) => {
        this.refreshFolderBrowser();
        if (this.propertiesUuid === e.detail.uuid) this.reloadPropertiesPanel();
      });

      window.addEventListener('shares-changed', () => this.reloadPropertiesPanel());
      // A contributed section of the panel changed what it renders.
      window.addEventListener('properties-section-changed', () => this.reloadPropertiesPanel());

      // alpine-ajax marks every swap target aria-busy for the life of its
      // request. Listening on the document, that attribute is what tells a
      // folder navigation (sidebar links live outside this component) apart
      // from a properties-panel or activity refresh.
      document.addEventListener('ajax:send', () => {
        if (document.getElementById('folder-browser')?.getAttribute('aria-busy') === 'true') {
          this.folderLoading = true;
        }
      });
      // Failures bubble up here for every request, and a properties panel
      // failing must not take the veil off a folder still loading. On
      // ajax:missing the event names its target. On ajax:error it does
      // not, but alpine-ajax releases the target's aria-busy right after
      // dispatching, so the attribute answers once this handler yields.
      document.addEventListener('ajax:missing', (e) => {
        if (e.detail?.target?.id === 'folder-browser') this.folderLoading = false;
      });
      document.addEventListener('ajax:error', () => {
        queueMicrotask(() => {
          if (document.getElementById('folder-browser')?.getAttribute('aria-busy') !== 'true') {
            this.folderLoading = false;
          }
        });
      });

      // Close properties panel on folder navigation (but not on same-folder
      // refresh, nor on a back or forward onto an entry that had it open)
      this._lastViewUrl = null;
      window.addEventListener('folder-browser-replaced', (e) => {
        this.folderLoading = false;
        const newUrl = e.detail?.viewUrl;
        if (this._lastViewUrl && newUrl && newUrl !== this._lastViewUrl
            && this.showPropertiesPanel && !this.propertiesAddressed()) {
          this.closePropertiesPanel();
        }
        this._lastViewUrl = newUrl;
      });
    }
  };
};

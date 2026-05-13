window.fileBrowser = function fileBrowser() {
  return {
    cleaningTrash: false,

    // Upload progress state
    uploading: false,
    uploadTotal: 0,
    uploadCompleted: 0,
    uploadCurrentFile: '',
    uploadBytePercent: 0,
    _uploadToastEl: null,
    _uploadToastTimer: null,

    _startLoading(...uuids) {
      if (window._actionLoadingState) {
        for (const uuid of uuids) window._actionLoadingState[uuid] = true;
      }
    },

    _stopLoading(...uuids) {
      if (window._actionLoadingState) {
        for (const uuid of uuids) delete window._actionLoadingState[uuid];
      }
    },

    // Properties panel state
    showPropertiesPanel: false,
    propertiesUuid: null,
    propertiesNodeType: 'file',
    propertiesLoading: false,
    propertiesError: null,

    get currentFolder() {
      const folderEl = document.getElementById('folder-browser');
      return folderEl?.dataset.folder || '';
    },

    openPropertiesPanel(uuid, nodeType) {
      // If same file and panel is already open, toggle close
      if (this.showPropertiesPanel && this.propertiesUuid === uuid) {
        this.closePropertiesPanel();
        return;
      }

      this.propertiesUuid = uuid;
      this.propertiesNodeType = nodeType || 'file';
      this.propertiesError = null;
      this.propertiesLoading = true;
      this.showPropertiesPanel = true;

      const onError = () => { this.propertiesError = 'Failed to load properties'; };
      const onAfter = () => { this.propertiesLoading = false; };
      this.$el.addEventListener('ajax:error', onError, { once: true });
      this.$el.addEventListener('ajax:after', onAfter, { once: true });
      this.$ajax(`/files/properties/${uuid}`, { target: 'properties-content' });
    },

    closePropertiesPanel() {
      this.showPropertiesPanel = false;
      this.propertiesUuid = null;
      this.propertiesError = null;
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
        }
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
          case 'download':
            this.bulkDownload(uuids);
            break;
        }
      });
    },

    openFolderFromRow(event) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      const row = event.currentTarget;
      const link = row?.querySelector('[data-folder-link]');
      if (link) {
        link.click();
      }
    },

    openFileFromRow(event, uuid, name, mimeType) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: { uuid, name, mime_type: mimeType }
      }));
    },

    openContextMenu(event, nodeData) {
      event.preventDefault();
      // Dispatch event for context menu to listen
      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
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
          title: `Delete ${nodeType}?`,
          message: `Move "${name}" to trash?${nodeType === 'folder' ? ' This will also move all contents.' : ''}`,
          okLabel: 'Move to trash',
          okClass: 'btn-error'
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
        this.showAlert('error', error.message || 'Failed to create folder');
      }
    },


    async createFile(name, fileType, customExt) {
      const trimmedName = (name || '').trim();
      if (!trimmedName) {
        this.showAlert('error', 'File name is required');
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
          this.showAlert('error', 'Custom extension is required');
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
          this.showAlert('error', data.detail || 'Failed to create file');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to create file');
      }
    },

    triggerUpload() {
      const input = document.getElementById('file-upload-input');
      if (input) {
        input.click();
      }
    },

    async handleUpload(event) {
      const files = event.target.files;
      if (!files.length) return;
      await this.uploadFiles(files);
    },

    async uploadFiles(files) {
      this.uploading = true;
      this.uploadTotal = files.length;
      this.uploadCompleted = 0;
      this.uploadBytePercent = 0;

      // Delay showing the progress toast so fast uploads don't flash it
      this._uploadToastTimer = setTimeout(() => {
        this._uploadToastEl = window.AppAlert.show({
          message: 'Preparing upload...',
          type: 'info',
          duration: 0,
          dismissible: false,
        });
        this._updateUploadToast();
      }, 1000);

      let uploaded = 0;

      for (const file of files) {
        this.uploadCurrentFile = file.name;
        this.uploadBytePercent = 0;
        this._updateUploadToast();

        try {
          await this._uploadFile(file);
          uploaded++;
        } catch (err) {
          this.showAlert('error', `Failed to upload ${file.name}${err.message ? ': ' + err.message : ''}`);
        }

        this.uploadCompleted++;
      }

      // Cancel pending toast timer if upload finished before it fired
      clearTimeout(this._uploadToastTimer);
      this._uploadToastTimer = null;

      // Dismiss progress toast
      if (this._uploadToastEl) {
        window.AppAlert.dismiss(this._uploadToastEl);
        this._uploadToastEl = null;
      }

      // Refresh first, then show success toast after a short delay
      // so the Alpine AJAX refresh doesn't interfere with the toast
      if (uploaded > 0) {
        this.refreshFolderBrowser();
        const msg = `Uploaded ${uploaded} file${uploaded > 1 ? 's' : ''}`;
        setTimeout(() => this.showAlert('success', msg), 600);
      }

      // Reset state
      this.uploading = false;
      this.uploadTotal = 0;
      this.uploadCompleted = 0;
      this.uploadCurrentFile = '';
      this.uploadBytePercent = 0;

      // Reset file input so re-selecting same files triggers change
      const input = document.getElementById('file-upload-input');
      if (input) input.value = '';
    },

    _uploadFile(file) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();
        formData.append('name', file.name);
        formData.append('node_type', 'file');
        formData.append('content', file);
        if (this.currentFolder) {
          formData.append('parent', this.currentFolder);
        }

        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            this.uploadBytePercent = Math.round((e.loaded / e.total) * 100);
            this._updateUploadToast();
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            let detail = 'Unknown error';
            try {
              const data = JSON.parse(xhr.responseText);
              detail = data.detail || detail;
            } catch (_) {}
            reject(new Error(detail));
          }
        };

        xhr.onerror = () => reject(new Error('Network error'));

        xhr.open('POST', '/api/v1/files');
        xhr.setRequestHeader('X-CSRFToken', getCSRFToken());
        xhr.send(formData);
      });
    },

    _updateUploadToast() {
      if (!this._uploadToastEl) return;

      // Build a standalone toast element instead of fighting .alert layout
      const el = this._uploadToastEl;
      el.className = 'shadow-lg rounded-xl text-white';
      el.style.cssText = 'width:22rem; max-width:calc(100vw - 2rem); padding:0; overflow:hidden; background:oklch(var(--in));';

      el.replaceChildren();

      // Inner padding container
      const inner = document.createElement('div');
      inner.style.cssText = 'display:flex; align-items:start; gap:0.75rem; padding:0.875rem 1rem;';

      // Upload icon
      const iconNS = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(iconNS, 'svg');
      svg.setAttribute('class', 'shrink-0');
      svg.setAttribute('width', '20');
      svg.setAttribute('height', '20');
      svg.setAttribute('fill', 'none');
      svg.setAttribute('stroke', 'currentColor');
      svg.setAttribute('stroke-width', '2');
      svg.setAttribute('stroke-linecap', 'round');
      svg.setAttribute('stroke-linejoin', 'round');
      svg.setAttribute('viewBox', '0 0 24 24');
      const path = document.createElementNS(iconNS, 'path');
      path.setAttribute('d', 'M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12');
      svg.appendChild(path);
      svg.style.marginTop = '1px';
      inner.appendChild(svg);

      // Text block
      const text = document.createElement('div');
      text.style.cssText = 'flex:1; min-width:0; overflow:hidden;';

      const header = document.createElement('div');
      header.style.cssText = 'display:flex; justify-content:space-between; align-items:baseline;';
      const title = document.createElement('span');
      title.className = 'font-semibold text-sm';
      title.textContent = `Uploading ${this.uploadCompleted + 1} / ${this.uploadTotal}`;
      const pct = document.createElement('span');
      pct.className = 'text-xs tabular-nums';
      pct.style.opacity = '0.8';
      pct.textContent = `${this.uploadBytePercent}%`;
      header.appendChild(title);
      header.appendChild(pct);
      text.appendChild(header);

      const nameEl = document.createElement('div');
      nameEl.className = 'text-xs truncate';
      nameEl.style.opacity = '0.7';
      nameEl.textContent = this.uploadCurrentFile;
      text.appendChild(nameEl);

      inner.appendChild(text);
      el.appendChild(inner);

      // Full-width progress bar pinned to the bottom, no side padding
      const track = document.createElement('div');
      track.style.cssText = 'height:5px; background:rgba(255,255,255,0.15);';
      const fill = document.createElement('div');
      fill.style.cssText = `height:100%; width:${this.uploadBytePercent}%; background:rgba(255,255,255,0.85); transition:width 0.15s ease;`;
      track.appendChild(fill);
      el.appendChild(track);
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
        this.showAlert('error', error.message || 'Failed to rename');
      } finally {
        this._stopLoading(uuid);
      }
    },

    async deleteItem(uuid) {
      this._startLoading(uuid);
      try {
        const response = await fetch(`/api/v1/files/${uuid}`, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to delete');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to delete');
      } finally {
        this._stopLoading(uuid);
      }
    },

    async confirmRestore(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: `Restore ${nodeType}?`,
        message: `Restore "${name}" from trash?`,
        okLabel: 'Restore',
        okClass: 'btn-primary'
      });
      if (confirmed) {
        this.restoreItem(uuid);
      }
    },

    async restoreItem(uuid) {
      this._startLoading(uuid);
      try {
        const response = await fetch(`/api/v1/files/${uuid}/restore`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to restore');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to restore');
      } finally {
        this._stopLoading(uuid);
      }
    },

    async confirmPurge(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: `Delete ${nodeType} permanently?`,
        message: `This will permanently delete "${name}" and cannot be undone.`,
        okLabel: 'Delete permanently',
        okClass: 'btn-error'
      });
      if (confirmed) {
        this.purgeItem(uuid);
      }
    },

    async purgeItem(uuid) {
      this._startLoading(uuid);
      try {
        const response = await fetch(`/api/v1/files/${uuid}/purge`, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to delete permanently');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to delete permanently');
      } finally {
        this._stopLoading(uuid);
      }
    },

    async confirmCleanTrash() {
      const confirmed = await AppDialog.confirm({
        title: 'Empty trash?',
        message: 'This will permanently delete all items in trash and cannot be undone.',
        okLabel: 'Empty trash',
        okClass: 'btn-error'
      });
      if (confirmed) {
        this.cleanTrash(true);
      }
    },

    async cleanTrash(force = false) {
      this.cleaningTrash = true;
      const url = force ? '/api/v1/files/trash/clean?force=1' : '/api/v1/files/trash/clean';
      try {
        const response = await fetch(url, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to clean trash');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to clean trash');
      } finally {
        this.cleaningTrash = false;
      }
    },

    async toggleFavorite(uuid, isFavorite) {
      if (!uuid) return;
      this._startLoading(uuid);
      try {
        const response = await fetch(`/api/v1/files/${uuid}/favorite`, {
          method: isFavorite ? 'DELETE' : 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
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
        this.showAlert('error', data.detail || 'Failed to update favorites');
      } catch (error) {
        this.showAlert('error', 'Failed to update favorites');
      } finally {
        this._stopLoading(uuid);
      }
    },

    async togglePin(uuid, isPinned) {
      if (!uuid) return;
      this._startLoading(uuid);
      try {
        const response = await fetch(`/api/v1/files/${uuid}/pin`, {
          method: isPinned ? 'DELETE' : 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
          return;
        }
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = {};
        }
        this.showAlert('error', data.detail || 'Failed to update pin');
      } catch (error) {
        this.showAlert('error', 'Failed to update pin');
      } finally {
        this._stopLoading(uuid);
      }
    },

    // Bulk actions
    async bulkDeleteItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      if (window.getFilePrefs().confirmBeforeDelete) {
        const count = uuids.length;
        const confirmed = await AppDialog.confirm({
          title: 'Delete Items',
          message: `Are you sure you want to delete ${count} item${count > 1 ? 's' : ''}? They will be moved to trash.`,
          confirmText: 'Delete',
          confirmClass: 'btn-error'
        });
        if (!confirmed) return;
      }

      this._startLoading(...uuids);
      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      if (errorCount > 0) {
        this.showAlert('warning', `Deleted ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `Deleted ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
      this._stopLoading(...uuids);
    },

    async bulkToggleFavorite(uuids, add) {
      if (!uuids || uuids.length === 0) return;

      this._startLoading(...uuids);
      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}/favorite`, {
            method: add ? 'POST' : 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      const action = add ? 'Added to' : 'Removed from';
      if (errorCount > 0) {
        this.showAlert('warning', `${action} favorites: ${successCount} succeeded, ${errorCount} failed`);
      } else {
        this.showAlert('success', `${action} favorites: ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
      this._stopLoading(...uuids);
    },

    async bulkRestoreItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      const count = uuids.length;
      const confirmed = await AppDialog.confirm({
        title: 'Restore items?',
        message: `Restore ${count} item${count > 1 ? 's' : ''} from trash?`,
        okLabel: 'Restore',
        okClass: 'btn-primary'
      });
      if (!confirmed) return;

      this._startLoading(...uuids);
      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}/restore`, {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      if (errorCount > 0) {
        this.showAlert('warning', `Restored ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `Restored ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
      this._stopLoading(...uuids);
    },

    async bulkPurgeItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      const count = uuids.length;
      const confirmed = await AppDialog.confirm({
        title: 'Delete permanently?',
        message: `This will permanently delete ${count} item${count > 1 ? 's' : ''} and cannot be undone.`,
        okLabel: 'Delete permanently',
        okClass: 'btn-error'
      });
      if (!confirmed) return;

      this._startLoading(...uuids);
      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}/purge`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      if (errorCount > 0) {
        this.showAlert('warning', `Permanently deleted ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `Permanently deleted ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
      this._stopLoading(...uuids);
    },

    async bulkDownload(uuids) {
      if (!uuids || uuids.length === 0) return;
      try {
        const csrfToken = getCSRFToken();
        const resp = await fetch('/api/v1/files/bulk-download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids }),
        });
        if (!resp.ok) {
          this.showAlert('error', 'Failed to download selected files');
          return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'download.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        this.showAlert('error', 'Failed to download selected files');
      }
    },

    async bulkTogglePin(uuids, add) {
      if (!uuids || uuids.length === 0) return;

      this._startLoading(...uuids);
      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}/pin`, {
            method: add ? 'POST' : 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      const action = add ? 'Pinned' : 'Unpinned';
      if (errorCount > 0) {
        this.showAlert('warning', `${action} ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `${action} ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
      this._stopLoading(...uuids);
    },

    // Clipboard operations
    cutToClipboard(items) {
      if (!items || items.length === 0) return;
      window.fileClipboard.cut(items);
      const count = items.length;
      this.showAlert('info', `${count} item${count > 1 ? 's' : ''} cut to clipboard`);
    },

    copyToClipboard(items) {
      if (!items || items.length === 0) return;
      window.fileClipboard.copy(items);
      const count = items.length;
      this.showAlert('info', `${count} item${count > 1 ? 's' : ''} copied to clipboard`);
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

    _getItemsFromUuids(uuids) {
      return uuids.map(uuid => {
        const row = document.querySelector(`tr[data-uuid="${uuid}"]`);
        return {
          uuid,
          name: row?.dataset.name || '',
          nodeType: row?.dataset.nodeType || 'file'
        };
      });
    },

    async pasteFromClipboard() {
      const items = window.fileClipboard.getItems();
      if (!items || items.length === 0) {
        this.showAlert('warning', 'Clipboard is empty');
        return;
      }

      const itemUuids = items.map(i => i.uuid);
      this._startLoading(...itemUuids);
      const isCopy = window.fileClipboard.isCopy();
      const targetFolderId = this.currentFolder || null;
      let successCount = 0;
      let errorCount = 0;

      for (const item of items) {
        try {
          let response;
          if (isCopy) {
            // Copy: duplicate the file/folder
            response = await fetch(`/api/v1/files/${item.uuid}/copy`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
              },
              body: JSON.stringify({ parent: targetFolderId })
            });
          } else {
            // Cut: move the file/folder
            response = await fetch(`/api/v1/files/${item.uuid}`, {
              method: 'PATCH',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
              },
              body: JSON.stringify({ parent: targetFolderId })
            });
          }
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      const action = isCopy ? 'Copied' : 'Moved';
      if (errorCount > 0) {
        this.showAlert('warning', `${action} ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `${action} ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      // Only clear clipboard on cut (move), keep it for copy
      if (!isCopy) {
        window.fileClipboard.clear();
      }
      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      this.refreshFolderBrowser();
      this._stopLoading(...itemUuids);
    },

    async pinFolder(uuid) {
      if (!uuid) return;
      this._startLoading(uuid);
      try {
        const response = await fetch(`/api/v1/files/${uuid}/pin`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
          return;
        }
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = {};
        }
        this.showAlert('error', data.detail || 'Failed to pin folder');
      } catch (error) {
        this.showAlert('error', 'Failed to pin folder');
      } finally {
        this._stopLoading(uuid);
      }
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

    showAlert(type, message) {
      if (window.AppAlert && typeof window.AppAlert.show === 'function') {
        window.AppAlert.show({
          type: type || 'info',
          message: message || '',
          duration: type === 'error' ? 8000 : 5000,
        });
        return;
      }
      console.warn('AppAlert is not available:', message);
    },


    init() {
      // Create reactive backing for the global isActionLoading() function.
      // Must happen here (not at top-level) because Alpine.reactive is only
      // available once Alpine has started.
      if (!window._actionLoadingState) {
        window._actionLoadingState = Alpine.reactive({});
      }

      this._initFileActions();
      // Listen for form submissions from dialogs
      window.addEventListener('create-folder', (e) => this.createFolder(e.detail.name));
      window.addEventListener('create-file', (e) => this.createFile(e.detail.name, e.detail.fileType, e.detail.customExt));
      window.addEventListener('rename-item', (e) => this.renameItem(e.detail.uuid, e.detail.name));
      window.addEventListener('create-group-folder', (e) => {
        window.fileActions.createGroupFolder(e.detail.groupId, e.detail.groupName)
          .then(() => window.dispatchEvent(new CustomEvent('group-folders-changed')))
          .catch((err) => this.showAlert('error', err.message || 'Failed to create group folder'));
      });

      // Properties panel events
      window.addEventListener('open-properties', (e) => {
        const { uuid, nodeType } = e.detail;
        this.openPropertiesPanel(uuid, nodeType);
      });

      window.addEventListener('shares-changed', () => {
        if (this.showPropertiesPanel && this.propertiesUuid) {
          this.openPropertiesPanel(this.propertiesUuid, this.propertiesNodeType);
        }
      });

      // Close properties panel on folder navigation (but not on same-folder refresh)
      this._lastViewUrl = null;
      window.addEventListener('folder-browser-replaced', (e) => {
        const newUrl = e.detail?.viewUrl;
        if (this._lastViewUrl && newUrl && newUrl !== this._lastViewUrl && this.showPropertiesPanel) {
          this.closePropertiesPanel();
        }
        this._lastViewUrl = newUrl;
      });
    }
  };
};

// --- Folder navigation history ---
// Uses two persistent hidden <a> elements (#folder-nav-push / #folder-nav-replace)
// to navigate via Alpine AJAX, just like clicking a folder link.
window.folderNav = {
  _stack: [],
  _index: -1,
  _skipPush: false,

  init() {
    this._stack = [window.location.pathname + window.location.search];
    this._index = 0;
    window.addEventListener('popstate', () => { this._skipPush = true; });
  },

  // Called from x-init on #folder-browser after every AJAX render
  onNavigate(url) {
    if (this._skipPush) {
      const idx = this._stack.lastIndexOf(url);
      if (idx !== -1) this._index = idx;
      this._skipPush = false;
    } else if (url !== this._stack[this._index]) {
      this._stack = this._stack.slice(0, this._index + 1);
      this._stack.push(url);
      this._index = this._stack.length - 1;
    }
    window.dispatchEvent(new Event('nav-state-changed'));
  },

  canGoBack()    { return this._index > 0; },
  canGoForward() { return this._index < this._stack.length - 1; },

  back() {
    if (!this.canGoBack()) return;
    this._skipPush = true;
    this._index--;
    this._clickNavLink(this._stack[this._index], false);
  },

  forward() {
    if (!this.canGoForward()) return;
    this._skipPush = true;
    this._index++;
    this._clickNavLink(this._stack[this._index], false);
  },

  navigateTo(url) {
    if (url) this._clickNavLink(url, true);
  },

  _clickNavLink(url, push) {
    const link = document.getElementById(push ? 'folder-nav-push' : 'folder-nav-replace');
    if (!link) return;
    link.href = url;
    link.click();
  },
};
window.folderNav.init();

// --- Navigation buttons Alpine component ---
window.navButtons = function navButtons() {
  return {
    canGoBack: false,
    canGoForward: false,
    parentUrl: '',

    init() {
      this._syncState();
      window.addEventListener('nav-state-changed', () => this._syncState());
    },

    _syncState() {
      this.canGoBack = window.folderNav.canGoBack();
      this.canGoForward = window.folderNav.canGoForward();
      this.parentUrl = document.getElementById('folder-browser')?.dataset.parentUrl || '';
    },

    navigateUp() {
      window.folderNav.navigateTo(this.parentUrl);
    },
  };
};

// --- Action loading state ---
// Global function + Alpine.reactive backing so it works regardless of
// nested x-data proxy resolution (Chrome V8 bug with Proxy + with()).
// Alpine.reactive() is initialised in fileBrowser().init().
window._actionLoadingState = null;
window.isActionLoading = function (uuid) {
  return !!window._actionLoadingState?.[uuid];
};

// --- File browser preferences ---
window._filePrefsDefaults = { showHiddenFiles: false, confirmBeforeDelete: true, defaultSort: 'default', defaultSortDir: 'asc', breadcrumbCollapse: 4, defaultViewMode: 'list', mosaicTileSize: 3 };
window._filePrefsCache = { ...window._filePrefsDefaults };

window.getFilePrefs = function() {
  return window._filePrefsCache;
};

window.filePreferences = function filePreferences() {
  const API_URL = '/api/v1/settings/files/preferences';

  return {
    prefs: { ...window._filePrefsCache },
    _saveTimer: null,

    init() {
      fetch(API_URL, { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data && data.value && typeof data.value === 'object') {
            this.prefs = { ...window._filePrefsDefaults, ...data.value };
            this._broadcast();
          }
        })
        .catch(() => {});
    },

    update(key, value) {
      this.prefs[key] = value;
      this._saveRemote();
      this._broadcast();
      // Breadcrumb collapse is rendered server-side; refresh to apply
      if (key === 'breadcrumbCollapse') {
        const link = document.querySelector('[data-refresh-folder-browser]');
        if (link) link.click();
      }
    },

    _broadcast() {
      window._filePrefsCache = { ...this.prefs };
      window.dispatchEvent(new CustomEvent('preferences-changed', { detail: this.prefs }));
    },

    _saveRemote() {
      clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
          || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
          || '';
        fetch(API_URL, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ value: this.prefs }),
        }).catch(() => {});
      }, 500);
    },
  };
};

window.sidebarCollapse = function sidebarCollapse() {
  return {
    collapsed: localStorage.getItem('sidebarCollapsed') === 'true',
    activeView: null,

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },
    
    init() {
      if (this.isMobile()) {
        this.collapsed = true;
      }
      this.syncActiveView();
      window.addEventListener('popstate', () => this.syncActiveView());
      window.addEventListener('nav-state-changed', () => this.syncActiveView());
      window.matchMedia('(max-width: 1023px)').addEventListener('change', (event) => {
        if (event.matches) {
          this.collapsed = true;
        }
      });

      // Initialize Lucide icons on load
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons();
        }
      });
      
      // Re-initialize Lucide icons when state changes
      this.$watch('collapsed', () => {
        // Wait for transition to complete before recreating icons
        setTimeout(() => {
          if (typeof lucide !== 'undefined') {
            lucide.createIcons();
          }
        }, 350); // Slightly longer than transition duration (300ms)
      });
    },
    
    toggleCollapse() {
      if (this.isMobile()) {
        return;
      }
      this.collapsed = !this.collapsed;
      localStorage.setItem('sidebarCollapsed', this.collapsed);
      
      // Immediate icon refresh for visible elements
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons();
        }
      });
    },

    syncActiveView() {
      const path = window.location.pathname.replace(/\/+$/, '');
      const params = new URLSearchParams(window.location.search);
      const favorites = (params.get('favorites') || '').toLowerCase();
      const recent = (params.get('recent') || '').toLowerCase();
      const shared = (params.get('shared') || '').toLowerCase();
      if (['1', 'true', 'yes'].includes(shared)) {
        this.activeView = 'shared';
        return;
      }
      if (['1', 'true', 'yes'].includes(favorites)) {
        this.activeView = 'favorites';
        return;
      }
      if (['1', 'true', 'yes'].includes(recent)) {
        this.activeView = 'recent';
        return;
      }
      if (path === '/files/trash') {
        this.activeView = 'trash';
        return;
      }
      if (path === '/files' || path.startsWith('/files/')) {
        this.activeView = 'root';
        return;
      }
      this.activeView = null;
    },

    setActiveView(view) {
      this.activeView = view;
    }
  }
}

// Global clipboard for cut/copy/paste operations
window.fileClipboard = {
  items: [],  // Array of {uuid, name, nodeType}
  mode: null, // 'cut' or 'copy'

  cut(items) {
    this.items = items;
    this.mode = 'cut';
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  copy(items) {
    this.items = items;
    this.mode = 'copy';
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  clear() {
    this.items = [];
    this.mode = null;
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  hasItems() {
    return this.items.length > 0;
  },

  getItems() {
    return this.items;
  },

  getMode() {
    return this.mode;
  },

  isCut() {
    return this.mode === 'cut';
  },

  isCopy() {
    return this.mode === 'copy';
  }
};

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

    async openPropertiesPanel(uuid, nodeType) {
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
      this.$refs.propertiesContent.replaceChildren();

      try {
        const response = await fetch(`/files/properties/${uuid}`);
        if (!response.ok) throw new Error('Failed to load properties');
        const html = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        this.$refs.propertiesContent.replaceChildren(...doc.body.children);
        this.$nextTick(() => lucide.createIcons());
      } catch (err) {
        this.propertiesError = err.message;
      } finally {
        this.propertiesLoading = false;
      }
    },

    closePropertiesPanel() {
      this.showPropertiesPanel = false;
      this.propertiesUuid = null;
      this.propertiesError = null;
      this.$refs.propertiesContent?.replaceChildren();
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
            this.confirmDelete(uuid, name, nodeType);
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
      const dialog = document.getElementById('create-folder-dialog');
      const input = dialog.querySelector('input');
      input.value = '';
      dialog.showModal();
      setTimeout(() => input.focus(), 100);
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
      const dialog = document.getElementById('rename-dialog');
      window.dispatchEvent(new CustomEvent('open-rename', { detail: { uuid, name } }));
      dialog.showModal();
      setTimeout(() => dialog.querySelector('input').focus(), 100);
    },

    async confirmDelete(uuid, name, nodeType) {
      if (window.getFilePrefs().confirmBeforeDelete) {
        const confirmed = await AppDialog.confirm({
          title: `Delete ${nodeType}?`,
          message: `Move "${name}" to trash?${nodeType === 'folder' ? ' This will also move all contents.' : ''}`,
          okLabel: 'Move to trash',
          okClass: 'btn-error'
        });
        if (!confirmed) return;
      }
      this.deleteItem(uuid);
    },

    async createFolder(name) {
      try {
        const response = await fetch('/api/v1/files', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.getCsrfToken()
          },
          body: JSON.stringify({
            name: name,
            node_type: 'folder',
            parent: this.currentFolder || null
          })
        });
        if (response.ok) {
          document.getElementById('create-folder-dialog').close();
          this.refreshFolderBrowser();
        } else {
          const data = await response.json();
          this.showAlert('error', data.detail || 'Failed to create folder');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to create folder');
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
            'X-CSRFToken': this.getCsrfToken()
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
        xhr.setRequestHeader('X-CSRFToken', this.getCsrfToken());
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
        const response = await fetch(`/api/v1/files/${uuid}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.getCsrfToken()
          },
          body: JSON.stringify({ name: newName })
        });
        if (response.ok) {
          document.getElementById('rename-dialog').close();
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          const data = await response.json();
          this.showAlert('error', data.detail || 'Failed to rename');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to rename');
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
            'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken()
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
            headers: { 'X-CSRFToken': this.getCsrfToken() }
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
            headers: { 'X-CSRFToken': this.getCsrfToken() }
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
            headers: { 'X-CSRFToken': this.getCsrfToken() }
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
            headers: { 'X-CSRFToken': this.getCsrfToken() }
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

    async bulkTogglePin(uuids, add) {
      if (!uuids || uuids.length === 0) return;

      this._startLoading(...uuids);
      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}/pin`, {
            method: add ? 'POST' : 'DELETE',
            headers: { 'X-CSRFToken': this.getCsrfToken() }
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
                'X-CSRFToken': this.getCsrfToken()
              },
              body: JSON.stringify({ parent: targetFolderId })
            });
          } else {
            // Cut: move the file/folder
            response = await fetch(`/api/v1/files/${item.uuid}`, {
              method: 'PATCH',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken()
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
            'X-CSRFToken': this.getCsrfToken(),
          },
        });
      } catch (error) {
        console.warn('Folder sync failed:', error);
      }

      this.refreshFolderBrowser();
      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
    },

    refreshFolderBrowser() {
      const refreshLink = document.querySelector('[data-refresh-folder-browser]');
      if (refreshLink) {
        refreshLink.click();
        return;
      }
      const target = document.getElementById('folder-browser');
      if (!target) return;
      fetch(window.location.href, {
        headers: {
          'X-Alpine-Request': 'true'
        }
      })
        .then((response) => {
          if (!response.ok) {
            this.showAlert('error', 'Failed to refresh items');
            return null;
          }
          return response.text();
        })
        .then((html) => {
          if (!html) return;
          const wrapper = document.createElement('div');
          wrapper.innerHTML = html;
          const fresh = wrapper.querySelector('#folder-browser');
          if (!fresh) {
            this.showAlert('error', 'Failed to refresh items');
            return;
          }
          if (window.Alpine?.mutateDom) {
            window.Alpine.mutateDom(() => { target.replaceWith(fresh); });
          } else {
            target.replaceWith(fresh);
          }
          if (window.Alpine?.initTree) {
            window.Alpine.initTree(fresh);
          }
          if (window.lucide?.createIcons) {
            window.lucide.createIcons({ nodes: [fresh] });
          }
        })
        .catch(() => this.showAlert('error', 'Failed to refresh items'));
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

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
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

window.fileTableControls = function fileTableControls() {
  return {
    storageKey: 'fileTableControls:v4',
    searchQuery: '',
    typeFilter: 'all',
    sortField: window._filePrefsCache.defaultSort || 'default',
    sortDir: window._filePrefsCache.defaultSortDir || 'asc',
    columns: [
      { id: 'select', label: 'Select', required: false },
      { id: 'icon', label: 'Type', required: true },
      { id: 'name', label: 'Name', required: true },
      { id: 'favorite', label: 'Fav', required: false },
      { id: 'size', label: 'Size', required: false },
      { id: 'created', label: 'Created', required: false },
      { id: 'modified', label: 'Modified', required: false },
      { id: 'actions', label: 'Actions', required: false }
    ],
    defaultColumnOrder: ['select', 'icon', 'name', 'favorite', 'size', 'created', 'modified', 'actions'],
    defaultColumnVisibility: {
      select: true,
      icon: true,
      name: true,
      favorite: true,
      size: true,
      created: false,
      modified: true,
      actions: true
    },
    columnOrder: ['select', 'icon', 'name', 'favorite', 'size', 'created', 'modified', 'actions'],
    columnVisibility: {
      select: true,
      icon: true,
      name: true,
      favorite: true,
      size: true,
      created: false,
      modified: true,
      actions: true
    },
    table: null,
    tbody: null,
    originalRows: [],
    showHiddenFiles: window._filePrefsCache.showHiddenFiles || false,
    ready: false,
    _initializing: true,
    _saveTimer: null,

    // Selection state
    selectedUuids: new Set(),
    lastSelectedUuid: null, // Track last selected item for shift-click range selection

    // Hovered row (for shortcuts when nothing is selected)
    hoveredUuid: null,

    // Clipboard state
    hasClipboardItems: false,

    // Actions state (fetched from API, keyed by UUID)
    actionsMap: {},
    actionsLoading: false,

    // Bulk actions (computed from actionsMap intersection)
    bulkActions: [],
    bulkActionsLoading: false,

    get orderedColumns() {
      return this.columnOrder
        .map((id) => this.columns.find((col) => col.id === id))
        .filter(Boolean);
    },

    init() {
      this.table = this.$el.querySelector('table');
      if (!this.table) {
        return;
      }
      this.tbody = this.table.querySelector('tbody');
      if (!this.tbody) {
        return;
      }
      this.originalRows = Array.from(this.tbody.querySelectorAll('tr'));
      this.initStorageKey();
      this.loadState();
      this.pruneMissingColumns();
      this.ready = true;
      this.applyAll();
      this._initializing = false;

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [this.$el] });
        }
      });

      this.$watch('searchQuery', () => this.applyRows());
      this.$watch('typeFilter', () => this.applyRows());
      this.$watch('sortField', () => this.applyRows());
      this.$watch('sortDir', () => this.applyRows());
      this.$watch('showHiddenFiles', () => this.applyRows());

      // Sync showHiddenFiles when preferences change
      window.addEventListener('preferences-changed', (e) => {
        const show = e.detail?.showHiddenFiles;
        if (typeof show === 'boolean' && show !== this.showHiddenFiles) {
          this.showHiddenFiles = show;
        }
      });

      // Clear selection after bulk actions
      window.addEventListener('clear-file-selection', () => {
        this.clearSelection();
      });

      // Track clipboard state
      window.addEventListener('clipboard-changed', () => {
        this.hasClipboardItems = window.fileClipboard.hasItems();
      });
      this.hasClipboardItems = window.fileClipboard.hasItems();

      // Compute bulk actions from actionsMap when selection changes
      this.$watch('selectedUuids', () => this._computeBulkActions());

      // Fetch actions for all visible rows
      this.fetchActions();

      // Keyboard shortcuts (single listener — remove previous on re-init)
      if (window._fileShortcutHandler) {
        window.removeEventListener('keydown', window._fileShortcutHandler);
      }
      window._fileShortcutHandler = (e) => this._handleKeyboardShortcut(e);
      window.addEventListener('keydown', window._fileShortcutHandler);
    },

    openFolderFromRow(event) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        const row = event.currentTarget;
        const uuid = row?.dataset?.uuid;
        if (uuid) {
          this.toggleRowSelection(uuid, event.shiftKey);
        }
        return;
      }
      // Normal behavior: open folder
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
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        this.toggleRowSelection(uuid, event.shiftKey);
        return;
      }
      // Normal behavior: open file viewer
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

    openBackgroundContextMenu(event) {
      // Check if click is on a row/card or interactive element
      const target = event.target;
      if (target.closest('tr[data-uuid], div[data-uuid].group, button, a, input, select, textarea')) {
        return; // Let the row/card handle its own context menu
      }

      event.preventDefault();
      // Dispatch event for background context menu
      window.dispatchEvent(new CustomEvent('open-background-context-menu', {
        detail: { event }
      }));
    },

    // Selection methods
    isSelected(uuid) {
      return this.selectedUuids.has(uuid);
    },

    toggleRowSelection(uuid, shiftKey = false) {
      // Shift-click: select range from last selected to current
      if (shiftKey && this.lastSelectedUuid && this.lastSelectedUuid !== uuid) {
        // Get all visible items in current view (filtered rows in list view, or all items in mosaic view)
        let allUuids = [];

        // Try to get from visible rows (after filtering/sorting)
        if (this.visibleRows && this.visibleRows.length > 0) {
          allUuids = this.visibleRows.map(r => r.dataset?.uuid).filter(Boolean);
        } else {
          // Fallback: get from DOM
          // For list view: tbody > tr[data-uuid]
          // For mosaic view: .grid > div[data-uuid]
          const tbody = document.querySelector('tbody tr[data-uuid]');
          const gridContainer = document.querySelector('div.grid div[data-uuid]');

          if (tbody) {
            // List view
            const items = Array.from(document.querySelectorAll('tbody tr[data-uuid]'));
            allUuids = items.map(r => r.dataset.uuid).filter(Boolean);
          } else if (gridContainer) {
            // Mosaic view
            const items = Array.from(document.querySelectorAll('div.grid > div[data-uuid]'));
            allUuids = items.map(r => r.dataset.uuid).filter(Boolean);
          }
        }

        const startIdx = allUuids.indexOf(this.lastSelectedUuid);
        const endIdx = allUuids.indexOf(uuid);

        if (startIdx !== -1 && endIdx !== -1) {
          const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
          for (let i = from; i <= to; i++) {
            this.selectedUuids.add(allUuids[i]);
          }
          this.lastSelectedUuid = uuid;
          this.selectedUuids = new Set(this.selectedUuids);
          return;
        }
      }

      // Normal toggle
      if (this.selectedUuids.has(uuid)) {
        this.selectedUuids.delete(uuid);
        // If we deselect the last selected, clear it
        if (this.lastSelectedUuid === uuid) {
          this.lastSelectedUuid = null;
        }
      } else {
        this.selectedUuids.add(uuid);
        this.lastSelectedUuid = uuid;
      }
      // Trigger reactivity
      this.selectedUuids = new Set(this.selectedUuids);
    },

    toggleSelectAll() {
      const visibleRows = Array.from(this.tbody.querySelectorAll('tr[data-uuid]'));
      const visibleUuids = visibleRows.map(r => r.dataset.uuid).filter(Boolean);

      const allSelected = visibleUuids.every(uuid => this.selectedUuids.has(uuid));

      if (allSelected) {
        // Deselect all visible
        visibleUuids.forEach(uuid => this.selectedUuids.delete(uuid));
      } else {
        // Select all visible
        visibleUuids.forEach(uuid => this.selectedUuids.add(uuid));
      }
      // Trigger reactivity
      this.selectedUuids = new Set(this.selectedUuids);
    },

    get selectAllState() {
      if (!this.tbody) return 'none';
      const visibleRows = Array.from(this.tbody.querySelectorAll('tr[data-uuid]'));
      const visibleUuids = visibleRows.map(r => r.dataset.uuid).filter(Boolean);
      if (visibleUuids.length === 0) return 'none';

      const selectedCount = visibleUuids.filter(uuid => this.selectedUuids.has(uuid)).length;
      if (selectedCount === 0) return 'none';
      if (selectedCount === visibleUuids.length) return 'all';
      return 'partial';
    },

    clearSelection() {
      this.selectedUuids = new Set();
    },

    getSelectedUuids() {
      return Array.from(this.selectedUuids);
    },

    getSelectedCount() {
      return this.selectedUuids.size;
    },

    // Fetch actions for all visible rows from the API
    async fetchActions() {
      // Get elements with data-uuid from both list view (tr) and mosaic view (div)
      const rows = this.originalRows.length
        ? this.originalRows
        : Array.from((this.tbody || this.$el).querySelectorAll('tr[data-uuid], div[data-uuid]'));
      const uuids = rows.map(r => r.dataset.uuid).filter(Boolean);
      if (uuids.length === 0) return;

      this.actionsLoading = true;
      try {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
          || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
          || '';
        const resp = await fetch('/api/v1/files/actions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids }),
        });
        if (resp.ok) {
          this.actionsMap = await resp.json();
          this._computeBulkActions();
        }
      } catch (e) {
        // silent
      } finally {
        this.actionsLoading = false;
      }
    },

    // Compute bulk actions: intersection of actions for all selected UUIDs, filtered to bulk-capable
    _computeBulkActions() {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) {
        this.bulkActions = [];
        return;
      }

      const lists = uuids.map(uuid => this.actionsMap[uuid] || []);
      if (lists.some(l => l.length === 0)) {
        this.bulkActions = [];
        return;
      }

      // Start with first file's bulk-capable actions, intersect with the rest
      let common = lists[0].filter(a => a.bulk);
      for (let i = 1; i < lists.length; i++) {
        const ids = new Set(lists[i].filter(a => a.bulk).map(a => a.id));
        common = common.filter(a => ids.has(a.id));
        if (common.length === 0) break;
      }

      const catOrder = ['transfer', 'organize', 'edit', 'danger', 'trash'];
      this.bulkActions = common.sort((a, b) => catOrder.indexOf(a.category) - catOrder.indexOf(b.category));

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [this.$el] });
      });
    },

    executeBulkAction(action) {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) return;

      switch (action.id) {
        case 'toggle_favorite': {
          const allFav = uuids.every(uuid => {
            const a = (this.actionsMap[uuid] || []).find(x => x.id === 'toggle_favorite');
            return a && a.state && a.state.is_favorite;
          });
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'favorite', uuids, add: !allFav }
          }));
          break;
        }
        case 'toggle_pin': {
          const allPinned = uuids.every(uuid => {
            const a = (this.actionsMap[uuid] || []).find(x => x.id === 'toggle_pin');
            return a && a.state && a.state.is_pinned;
          });
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'pin', uuids, add: !allPinned }
          }));
          break;
        }
        case 'download':
          this._bulkDownload(uuids);
          break;
        case 'cut':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'cut', uuids }
          }));
          break;
        case 'copy':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'copy', uuids }
          }));
          break;
        case 'delete':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'delete', uuids }
          }));
          break;
        case 'restore':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'restore', uuids }
          }));
          break;
        case 'purge':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'purge', uuids }
          }));
          break;
      }
    },

    async _bulkDownload(uuids) {
      try {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
          || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
          || '';
        const resp = await fetch('/api/v1/files/bulk-download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids }),
        });
        if (!resp.ok) return;
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
        // silent
      }
    },

    bulkPaste() {
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'paste' }
      }));
    },

    // --- Keyboard shortcuts ---

    _isInputFocused() {
      const el = document.activeElement;
      if (!el) return false;
      const tag = el.tagName;
      if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
      if (tag === 'INPUT') {
        const type = (el.type || '').toLowerCase();
        // Checkboxes and radios don't accept text — allow shortcuts
        if (type === 'checkbox' || type === 'radio') return false;
        return true;
      }
      if (el.isContentEditable) return true;
      return false;
    },

    _getRowDataByUuid(uuid) {
      if (!uuid) return null;
      const row = document.querySelector(`tr[data-uuid="${uuid}"]`);
      if (!row) return null;
      return {
        uuid,
        name: row.dataset.displayName || row.dataset.name || '',
        nodeType: row.dataset.nodeType || 'file',
        mimeType: row.dataset.mimeType || '',
        actions: this.actionsMap[uuid] || [],
      };
    },

    _nodeHasAction(data, actionId) {
      return data && data.actions && data.actions.some(a => a.id === actionId);
    },

    _getActionState(data, actionId) {
      if (!data || !data.actions) return {};
      const action = data.actions.find(a => a.id === actionId);
      return action && action.state ? action.state : {};
    },

    _handleKeyboardShortcut(e) {
      // Never interfere with events inside open dialogs
      if (e.target.closest('dialog[open]')) return;
      // Skip when typing in text inputs
      if (this._isInputFocused()) return;

      const ctrl = e.ctrlKey || e.metaKey;
      const key = e.key;

      // Alt+Arrow navigation shortcuts (before any selection/hover checks)
      if (e.altKey && key === 'ArrowLeft') {
        e.preventDefault();
        window.folderNav.back();
        return;
      }
      if (e.altKey && key === 'ArrowRight') {
        e.preventDefault();
        window.folderNav.forward();
        return;
      }
      if (e.altKey && key === 'ArrowUp') {
        e.preventDefault();
        const parentUrl = document.getElementById('folder-browser')?.dataset.parentUrl;
        if (parentUrl) window.folderNav.navigateTo(parentUrl);
        return;
      }

      const count = this.getSelectedCount();

      // Determine target: selection takes priority, then hovered row
      const useHover = count === 0 && this.hoveredUuid;

      // --- Shortcuts that don't require any target ---

      // Ctrl+V: Paste
      if (ctrl && key === 'v') {
        if (window.fileClipboard.hasItems()) {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('folder-action', {
            detail: { action: 'paste' }
          }));
        }
        return;
      }

      // No target at all — nothing to act on
      if (count === 0 && !useHover) return;

      // --- Multi-selection shortcuts (selected items only) ---
      if (count > 1) {
        const uuids = this.getSelectedUuids();

        if (key === 'Delete') {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'delete', uuids }
          }));
          return;
        }

        if (ctrl && key === 'x') {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'cut', uuids }
          }));
          return;
        }

        if (ctrl && key === 'c') {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'copy', uuids }
          }));
          return;
        }

        if ((key === 'f' || key === 'F') && !ctrl) {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'favorite', uuids, add: true }
          }));
          return;
        }

        // No single-item shortcuts for multi-selection
        return;
      }

      // --- Single target: 1 selected item OR hovered item ---
      const targetUuid = useHover ? this.hoveredUuid : this.getSelectedUuids()[0];
      const data = this._getRowDataByUuid(targetUuid);
      if (!data) return;

      // Delete
      if (key === 'Delete' && this._nodeHasAction(data, 'delete')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'delete', uuid: data.uuid, name: data.name, nodeType: data.nodeType }
        }));
        return;
      }

      // Ctrl+X: Cut
      if (ctrl && key === 'x' && this._nodeHasAction(data, 'cut')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'cut', uuid: data.uuid, name: data.name, nodeType: data.nodeType }
        }));
        return;
      }

      // Ctrl+C: Copy
      if (ctrl && key === 'c' && this._nodeHasAction(data, 'copy')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'copy', uuid: data.uuid, name: data.name, nodeType: data.nodeType }
        }));
        return;
      }

      // F: Toggle favorite
      if ((key === 'f' || key === 'F') && !ctrl && this._nodeHasAction(data, 'toggle_favorite')) {
        e.preventDefault();
        const state = this._getActionState(data, 'toggle_favorite');
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'toggle_favorite', uuid: data.uuid, state }
        }));
        return;
      }

      // Enter / Space: Open folder or view file
      if (key === 'Enter' || key === ' ') {
        e.preventDefault();
        if (this._nodeHasAction(data, 'open')) {
          const link = document.querySelector(`tr[data-uuid="${data.uuid}"] a[data-folder-link]`);
          if (link) link.click();
        } else if (this._nodeHasAction(data, 'view')) {
          window.dispatchEvent(new CustomEvent('open-file-viewer', {
            detail: { uuid: data.uuid, name: data.name, mime_type: data.mimeType }
          }));
        }
        return;
      }

      // F2: Rename
      if (key === 'F2' && this._nodeHasAction(data, 'rename')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'rename', uuid: data.uuid, name: data.name }
        }));
        return;
      }

      // P: Pin/unpin (folders only)
      if ((key === 'p' || key === 'P') && !ctrl && this._nodeHasAction(data, 'toggle_pin')) {
        e.preventDefault();
        const state = this._getActionState(data, 'toggle_pin');
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'toggle_pin', uuid: data.uuid, state }
        }));
        return;
      }

      // Ctrl+I: Properties
      if (ctrl && key === 'i' && this._nodeHasAction(data, 'properties')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('open-properties', {
          detail: { uuid: data.uuid, nodeType: data.nodeType }
        }));
        return;
      }
    },

    initStorageKey() {
      const baseKey = this.storageKey;
      const columns = Array.from(this.table.querySelectorAll('thead th[data-col]'))
        .map((cell) => cell.dataset.col)
        .filter(Boolean);
      if (columns.length) {
        this.storageKey = `${baseKey}:${columns.join('|')}`;
      } else {
        this.storageKey = baseKey;
      }
    },

    _applyStateData(data) {
      if (!data) return;
      if (Array.isArray(data.columnOrder)) {
        this.columnOrder = data.columnOrder.slice();
      }
      if (data.columnVisibility && typeof data.columnVisibility === 'object') {
        this.columnVisibility = { ...this.columnVisibility, ...data.columnVisibility };
      }
      if (typeof data.sortField === 'string') {
        this.sortField = data.sortField;
      }
      if (data.sortDir === 'asc' || data.sortDir === 'desc') {
        this.sortDir = data.sortDir;
      }
    },

    loadState() {
      // 1. Apply localStorage immediately (synchronous, avoids flicker)
      try {
        const raw = localStorage.getItem(this.storageKey);
        if (raw) this._applyStateData(JSON.parse(raw));
      } catch (e) { /* ignore */ }

      // 2. Apply cached server state if available (already fetched this session)
      if (window._tableControlsCache) {
        this._applyStateData(window._tableControlsCache);
        this.pruneMissingColumns();
        this.applyAll();
        this._saveToLocalStorage();
        this._lastSyncedState = JSON.stringify(this._getStatePayload());
        return;
      }

      // 3. First load: fetch from server, cache for subsequent navigations
      fetch('/api/v1/settings/files/table_controls')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data || !data.value) return;
          window._tableControlsCache = data.value;
          this._applyStateData(data.value);
          this.pruneMissingColumns();
          this.applyAll();
          this._saveToLocalStorage();
          this._lastSyncedState = JSON.stringify(this._getStatePayload());
        })
        .catch(() => {});
    },

    _getStatePayload() {
      return {
        sortField: this.sortField,
        sortDir: this.sortDir,
        columnOrder: this.columnOrder,
        columnVisibility: this.columnVisibility
      };
    },

    _saveToLocalStorage() {
      try {
        localStorage.setItem(this.storageKey, JSON.stringify(this._getStatePayload()));
      } catch (e) { /* ignore */ }
    },

    saveState() {
      // Always keep localStorage in sync (fast, synchronous)
      this._saveToLocalStorage();
      // Skip server persist during initial load
      if (this._initializing) return;
      // Debounce server persist (500ms) to batch rapid changes
      clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => {
        const payload = this._getStatePayload();
        const serialized = JSON.stringify(payload);
        if (serialized === this._lastSyncedState) return;
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
          || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
          || '';
        fetch('/api/v1/settings/files/table_controls', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ value: payload }),
        })
          .then(() => { this._lastSyncedState = serialized; window._tableControlsCache = payload; })
          .catch(() => {});
      }, 500);
    },

    pruneMissingColumns() {
      const present = new Set(
        Array.from(this.table.querySelectorAll('thead th[data-col]')).map((cell) => cell.dataset.col)
      );
      this.columns = this.columns.filter((col) => present.has(col.id));
      this.columnOrder = this.columnOrder.filter((id) => present.has(id));

      const missing = this.columns
        .map((col) => col.id)
        .filter((id) => !this.columnOrder.includes(id));
      this.columnOrder.push(...missing);

      this.columns.forEach((col) => {
        if (!(col.id in this.columnVisibility)) {
          this.columnVisibility[col.id] = true;
        }
        if (col.required) {
          this.columnVisibility[col.id] = true;
        }
      });
    },

    applyAll() {
      if (!this.ready) return;
      this.applyColumns();
      this.applyRows();
    },

    applyColumns() {
      if (!this.ready) return;
      this.applyColumnVisibility();
      this.applyColumnOrder();
    },

    applyColumnVisibility() {
      if (!this.table) return;
      this.columns.forEach((col) => {
        const show = this.isColumnVisible(col.id);
        this.table.querySelectorAll(`[data-col="${col.id}"]`).forEach((cell) => {
          cell.style.display = show ? '' : 'none';
        });
      });
      this.updateEmptyRowColspan();
    },

    applyColumnOrder() {
      if (!this.table) return;
      const headerRow = this.table.querySelector('thead tr');
      if (headerRow) {
        this.reorderRowCells(headerRow);
      }
      if (!this.tbody) return;
      const rows = Array.from(this.tbody.querySelectorAll('tr')).filter((row) => !row.dataset.emptyRow);
      rows.forEach((row) => this.reorderRowCells(row));
    },

    reorderRowCells(row) {
      const cells = Array.from(row.children).filter((cell) => cell.dataset && cell.dataset.col);
      if (!cells.length) return;
      const cellMap = new Map(cells.map((cell) => [cell.dataset.col, cell]));
      this.columnOrder.forEach((id) => {
        const cell = cellMap.get(id);
        if (cell) {
          row.appendChild(cell);
        }
      });
    },

    isColumnVisible(id) {
      const column = this.columns.find((col) => col.id === id);
      if (column && column.required) return true;
      return this.columnVisibility[id] !== false;
    },

    setColumnVisible(id, visible) {
      const column = this.columns.find((col) => col.id === id);
      if (column && column.required) {
        this.columnVisibility[id] = true;
        return;
      }
      this.columnVisibility[id] = Boolean(visible);
      this.applyColumnVisibility();
      this.saveState();
    },

    moveColumn(id, direction) {
      const index = this.columnOrder.indexOf(id);
      if (index === -1) return;
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= this.columnOrder.length) return;
      this.columnOrder.splice(index, 1);
      this.columnOrder.splice(nextIndex, 0, id);
      this.applyColumnOrder();
      this.saveState();
    },

    resetColumns() {
      this.columnOrder = this.defaultColumnOrder.filter((id) => this.columns.some((col) => col.id === id));
      this.columnVisibility = { ...this.defaultColumnVisibility };
      this.columns.forEach((col) => {
        if (!(col.id in this.columnVisibility)) {
          this.columnVisibility[col.id] = true;
        }
        if (col.required) {
          this.columnVisibility[col.id] = true;
        }
      });
      this.applyColumns();
      this.saveState();
    },

    resetAll() {
      this.searchQuery = '';
      this.typeFilter = 'all';
      this.sortField = window._filePrefsCache.defaultSort || 'default';
      this.sortDir = window._filePrefsCache.defaultSortDir || 'asc';
      this.resetColumns();
      this.applyRows();
    },

    toggleSortDir() {
      this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    },

    applyRows() {
      if (!this.ready || !this.tbody) return;
      const query = (this.searchQuery || '').trim().toLowerCase();
      const filtered = this.originalRows.filter((row) => this.matchesFilter(row, query));
      let ordered = filtered;

      if (this.sortField !== 'default') {
        const dir = this.sortDir === 'asc' ? 1 : -1;
        ordered = filtered.slice().sort((a, b) => this.compareRows(a, b, dir));
      }

      const fragment = document.createDocumentFragment();
      ordered.forEach((row) => fragment.appendChild(row));
      this.tbody.replaceChildren();
      this.tbody.appendChild(fragment);
      this.updateEmptyRow(ordered.length);
      this.saveState();
    },

    matchesFilter(row, query) {
      if (row.dataset.emptyRow) return false;
      const name = row.dataset.name || '';
      if (!this.showHiddenFiles && name.startsWith('.')) {
        return false;
      }
      if (query) {
        if (!name.includes(query)) {
          return false;
        }
      }
      if (this.typeFilter === 'files' && row.dataset.nodeType !== 'file') {
        return false;
      }
      if (this.typeFilter === 'folders' && row.dataset.nodeType !== 'folder') {
        return false;
      }
      if (this.typeFilter === 'favorites' && row.dataset.favorite !== '1') {
        return false;
      }
      return true;
    },

    compareRows(a, b, dir) {
      const aValue = this.getSortValue(a);
      const bValue = this.getSortValue(b);
      if (typeof aValue === 'string' || typeof bValue === 'string') {
        const diff = String(aValue).localeCompare(String(bValue));
        if (diff !== 0) return diff * dir;
      } else {
        if (aValue < bValue) return -1 * dir;
        if (aValue > bValue) return 1 * dir;
      }
      const aName = a.dataset.name || '';
      const bName = b.dataset.name || '';
      return aName.localeCompare(bName) * dir;
    },

    getSortValue(row) {
      switch (this.sortField) {
        case 'name':
          return row.dataset.name || '';
        case 'size':
          return parseInt(row.dataset.size || '0', 10);
        case 'created':
          return parseInt(row.dataset.created || '0', 10);
        case 'modified':
          return parseInt(row.dataset.updated || '0', 10);
        case 'favorite':
          return parseInt(row.dataset.favorite || '0', 10);
        case 'type':
          return row.dataset.nodeType === 'folder' ? 0 : 1;
        default:
          return 0;
      }
    },

    getColumnCount() {
      const headerCells = this.table.querySelectorAll('thead th[data-col]');
      if (headerCells.length) return headerCells.length;
      return this.columnOrder.length || 1;
    },

    updateEmptyRowColspan() {
      if (!this.tbody) return;
      const emptyRow = this.tbody.querySelector('[data-empty-row]');
      if (!emptyRow) return;
      const cell = emptyRow.querySelector('td');
      if (!cell) return;
      cell.setAttribute('colspan', String(this.getColumnCount()));
    },

    updateEmptyRow(visibleCount) {
      if (!this.tbody) return;
      let emptyRow = this.tbody.querySelector('[data-empty-row]');
      if (visibleCount === 0) {
        if (!emptyRow) {
          emptyRow = document.createElement('tr');
          emptyRow.dataset.emptyRow = 'true';
          const cell = document.createElement('td');
          cell.className = 'py-6 text-center text-base-content/60';
          cell.textContent = 'No matching items';
          cell.setAttribute('colspan', String(this.getColumnCount()));
          emptyRow.appendChild(cell);
        }
        emptyRow.querySelector('td').setAttribute('colspan', String(this.getColumnCount()));
        this.tbody.appendChild(emptyRow);
      } else if (emptyRow) {
        emptyRow.remove();
      }
    }
  };
};

window.pinnedFoldersSection = function pinnedFoldersSection() {
  return {
    dragOver: false,
    dragCounter: 0,
    pinnedCount: 0,
    // For reordering pinned folders
    draggingPinned: null,
    dragOverPinned: null,

    init() {
      // Set initial count from server-rendered list
      const list = document.getElementById('pinned-folders-list');
      this.pinnedCount = list ? list.children.length : 0;

      window.addEventListener('pinned-folders-changed', () => this.refreshPinnedSection());

      // Listen for pinned folder context menu events
      window.addEventListener('open-pinned-folder-context-menu', (e) => {
        this.openContextMenu(e.detail.event, {
          uuid: e.detail.uuid,
          name: e.detail.name,
          nodeType: 'folder',
          mimeType: '',
          isFavorite: e.detail.isFavorite,
          isViewable: false,
          isTrash: false,
          isPinned: true
        });
      });

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [this.$el] });
        }
      });
    },

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
    },

    onDragOver(event) {
      if (!event.dataTransfer.types.includes('application/x-pin-folder')) return;
      event.dataTransfer.dropEffect = 'link';
    },

    onDragEnter(event) {
      if (!event.dataTransfer.types.includes('application/x-pin-folder')) return;
      this.dragCounter++;
      this.dragOver = true;
    },

    onDragLeave(event) {
      this.dragCounter--;
      if (this.dragCounter <= 0) {
        this.dragCounter = 0;
        this.dragOver = false;
      }
    },

    async onDrop(event) {
      this.dragOver = false;
      this.dragCounter = 0;
      const raw = event.dataTransfer.getData('application/x-pin-folder');
      if (!raw) return;
      try {
        const data = JSON.parse(raw);
        if (!data.uuid) return;
        const response = await fetch(`/api/v1/files/${data.uuid}/pin`, {
          method: 'POST',
          headers: { 'X-CSRFToken': this.getCsrfToken() }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          const refreshLink = document.querySelector('[data-refresh-folder-browser]');
          if (refreshLink) refreshLink.click();
        } else {
          let errData = {};
          try { errData = await response.json(); } catch (e) {}
          if (window.AppAlert) {
            window.AppAlert.show({ type: 'error', message: errData.detail || 'Failed to pin folder', duration: 5000 });
          }
        }
      } catch (error) {
        if (window.AppAlert) {
          window.AppAlert.show({ type: 'error', message: 'Failed to pin folder', duration: 5000 });
        }
      }
    },

    async refreshPinnedSection() {
      try {
        const response = await fetch('/files/pinned');
        if (!response.ok) return;
        const html = await response.text();
        const currentList = document.getElementById('pinned-folders-list');
        if (!currentList) return;

        // Parse HTML safely using DOMParser and extract children
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const newItems = Array.from(doc.body.children);

        // Replace content with parsed DOM nodes
        currentList.replaceChildren(...newItems);
        this.pinnedCount = currentList.querySelectorAll('li').length;

        // Re-initialize Lucide icons
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [currentList] });
        }
        // Re-init Alpine on new content
        if (window.Alpine?.initTree) {
          window.Alpine.initTree(currentList);
        }
      } catch (error) {
        // Silent fail for sidebar refresh
      }
    },

    async openContextMenu(event, nodeData) {
      event.preventDefault();

      // Load actions for the pinned folder
      try {
        const csrfToken = this.getCsrfToken();
        const resp = await fetch('/api/v1/files/actions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids: [nodeData.uuid] }),
        });
        if (resp.ok) {
          const actionsMap = await resp.json();
          nodeData.actions = actionsMap[nodeData.uuid] || [];
        }
      } catch (e) {
        // Fallback to empty actions
        nodeData.actions = [];
      }

      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
    },

    // Pinned folder reordering methods
    onPinnedDragStart(event, uuid) {
      this.draggingPinned = uuid;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('application/x-reorder-pinned', uuid);
    },

    onPinnedDragEnd(event) {
      this.draggingPinned = null;
      this.dragOverPinned = null;
    },

    onPinnedDragOver(event, uuid) {
      if (!this.draggingPinned || this.draggingPinned === uuid) {
        this.dragOverPinned = null;
        return;
      }
      event.dataTransfer.dropEffect = 'move';
      this.dragOverPinned = uuid;
    },

    async onPinnedDrop(event, targetUuid) {
      const sourceUuid = this.draggingPinned;
      if (!sourceUuid || sourceUuid === targetUuid) {
        this.draggingPinned = null;
        this.dragOverPinned = null;
        return;
      }

      // Get current order from DOM
      const list = document.getElementById('pinned-folders-list');
      const items = Array.from(list.querySelectorAll('li.pinned-folder-item'));
      const uuids = items.map(li => li.dataset.pinnedUuid);

      // Calculate new order
      const sourceIndex = uuids.indexOf(sourceUuid);
      const targetIndex = uuids.indexOf(targetUuid);
      if (sourceIndex === -1 || targetIndex === -1) return;

      // Remove source and insert at target position
      uuids.splice(sourceIndex, 1);
      uuids.splice(targetIndex, 0, sourceUuid);

      // Optimistically reorder in DOM
      const sourceItem = items[sourceIndex];
      const targetItem = items[targetIndex];
      if (sourceIndex < targetIndex) {
        targetItem.after(sourceItem);
      } else {
        targetItem.before(sourceItem);
      }

      this.draggingPinned = null;
      this.dragOverPinned = null;

      // Save new order to server
      try {
        const response = await fetch('/api/v1/files/pinned/reorder', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.getCsrfToken()
          },
          body: JSON.stringify({ order: uuids })
        });
        if (!response.ok) {
          // Revert on error - refresh from server
          this.refreshPinnedSection();
        }
      } catch (error) {
        // Revert on error - refresh from server
        this.refreshPinnedSection();
      }
    }
  };
};

// userSelector is now provided globally by common/static/ui/js/user_selector.js

window.shareModal = function shareModal() {
  return {
    open: false,
    fileUuid: null,
    fileName: '',
    shares: [],        // existing shares from server
    pendingAdds: [],   // users to add (staged), each has { ...user, permission: 'ro' }
    pendingRemovals: new Set(), // user IDs to remove (staged)
    pendingPermissionChanges: new Map(), // userId → newPermission for existing shares
    loading: false,
    saving: false,

    get displayList() {
      const permChanges = this.pendingPermissionChanges;
      const existing = this.shares.map(s => ({
        ...s,
        permission: permChanges.has(s.id) ? permChanges.get(s.id) : s.permission,
        _pending: false,
        _removed: this.pendingRemovals.has(s.id),
      }));
      const added = this.pendingAdds.map(u => ({
        id: u.id,
        username: u.username,
        first_name: u.first_name,
        last_name: u.last_name,
        permission: u.permission || 'ro',
        _pending: true,
        _removed: false,
      }));
      return [...existing, ...added];
    },

    get hasChanges() {
      return this.pendingAdds.length > 0 || this.pendingRemovals.size > 0 || this.pendingPermissionChanges.size > 0;
    },

    init() {
      window.addEventListener('open-share-modal', (e) => {
        this.fileUuid = e.detail.uuid;
        this.fileName = e.detail.name;
        this.open = true;
        this.pendingAdds = [];
        this.pendingRemovals = new Set();
        this.pendingPermissionChanges = new Map();
        this.loadShares();
        this.$nextTick(() => {
          const dlg = this.$refs.shareDialog;
          if (dlg && !dlg.open) dlg.showModal();
          if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [this.$el] });
        });
      });

      window.addEventListener('share-user-selected', (e) => {
        this.stageAdd(e.detail.user);
      });
    },

    async loadShares() {
      if (!this.fileUuid) return;
      this.loading = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/shares`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.shares = await resp.json();
        }
      } catch (e) {
        this.shares = [];
      } finally {
        this.loading = false;
      }
    },

    stageAdd(user) {
      if (!user) return;
      // Already in existing shares?
      if (this.shares.some(s => s.id === user.id)) {
        // If it was marked for removal, undo that
        if (this.pendingRemovals.has(user.id)) {
          this.pendingRemovals.delete(user.id);
          this.pendingRemovals = new Set(this.pendingRemovals);
        }
        return;
      }
      // Already in pending adds?
      if (this.pendingAdds.some(u => u.id === user.id)) return;
      this.pendingAdds = [...this.pendingAdds, { ...user, permission: 'ro' }];
    },

    stageRemove(userId) {
      // If it's a pending add, just remove from the list
      const idx = this.pendingAdds.findIndex(u => u.id === userId);
      if (idx !== -1) {
        this.pendingAdds = this.pendingAdds.filter(u => u.id !== userId);
        return;
      }
      // Otherwise mark existing share for removal
      this.pendingRemovals.add(userId);
      this.pendingRemovals = new Set(this.pendingRemovals);
    },

    undoRemove(userId) {
      this.pendingRemovals.delete(userId);
      this.pendingRemovals = new Set(this.pendingRemovals);
    },

    stagePermissionChange(userId, permission, isPending) {
      if (isPending) {
        // Update permission on pending add
        this.pendingAdds = this.pendingAdds.map(u =>
          u.id === userId ? { ...u, permission } : u
        );
        return;
      }
      // For existing shares, check if it differs from original
      const original = this.shares.find(s => s.id === userId);
      if (original && original.permission === permission) {
        this.pendingPermissionChanges.delete(userId);
      } else {
        this.pendingPermissionChanges.set(userId, permission);
      }
      this.pendingPermissionChanges = new Map(this.pendingPermissionChanges);
    },

    async save() {
      if (!this.fileUuid || !this.hasChanges) return;
      this.saving = true;
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
        || '';
      const headers = {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      };
      let errors = 0;

      // Process additions
      for (const user of this.pendingAdds) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'POST', headers,
            body: JSON.stringify({ shared_with: user.id, permission: user.permission || 'ro' }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      // Process permission changes for existing shares
      for (const [userId, permission] of this.pendingPermissionChanges) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'POST', headers,
            body: JSON.stringify({ shared_with: userId, permission }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      // Process removals
      for (const userId of this.pendingRemovals) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'DELETE', headers,
            body: JSON.stringify({ shared_with: userId }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      this.saving = false;

      if (errors > 0 && window.AppAlert) {
        window.AppAlert.error(`Some changes failed (${errors} error${errors > 1 ? 's' : ''})`);
      } else if (window.AppAlert) {
        window.AppAlert.success('Sharing updated');
      }

      // Reset staged changes, reload, and close
      this.pendingAdds = [];
      this.pendingRemovals = new Set();
      this.pendingPermissionChanges = new Map();
      await this.loadShares();
      window.dispatchEvent(new CustomEvent('shares-changed'));
      // Refresh the folder browser so the shared badge updates
      const refreshLink = document.querySelector('[data-refresh-folder-browser]');
      if (refreshLink) refreshLink.click();
      this.closeModal();
    },

    closeModal() {
      this.open = false;
      const dlg = this.$refs.shareDialog;
      if (dlg && dlg.open) dlg.close();
      this.fileUuid = null;
      this.fileName = '';
      this.shares = [];
      this.pendingAdds = [];
      this.pendingRemovals = new Set();
      this.pendingPermissionChanges = new Map();
    },
  };
};

// --- Combined file table with view toggle ---
window.fileTableWithView = function fileTableWithView() {
  const tableControls = fileTableControls();
  const viewControls = viewToggle();

  // Merge init methods
  const tableInit = tableControls.init;
  const viewInit = viewControls.init;

  return {
    ...tableControls,
    ...viewControls,

    // Method to check if a card should be visible in mosaic view (respects filters)
    shouldShowCard(name, nodeType, isFavorite) {
      // Hidden files filter
      if (!this.showHiddenFiles && name.startsWith('.')) {
        return false;
      }
      // Search query filter
      const query = (this.searchQuery || '').trim().toLowerCase();
      if (query && !name.includes(query)) {
        return false;
      }
      // Type filter
      if (this.typeFilter === 'files' && nodeType !== 'file') {
        return false;
      }
      if (this.typeFilter === 'folders' && nodeType !== 'folder') {
        return false;
      }
      if (this.typeFilter === 'favorites' && isFavorite !== '1') {
        return false;
      }
      return true;
    },

    init() {
      // Call both init methods
      if (tableInit) tableInit.call(this);
      if (viewInit) viewInit.call(this);
    }
  };
};

// --- View toggle component ---
window.viewToggle = function viewToggle() {
  return {
    viewMode: 'list',
    mosaicTileSize: 3,

    init() {
      // Initialize from user preferences
      const prefs = window.getFilePrefs();
      this.viewMode = prefs.defaultViewMode || 'list';
      this.mosaicTileSize = prefs.mosaicTileSize || 3;

      // Listen for preference changes
      window.addEventListener('preferences-changed', (e) => {
        if (e.detail) {
          if (e.detail.defaultViewMode) this.viewMode = e.detail.defaultViewMode;
          if (e.detail.mosaicTileSize) this.mosaicTileSize = e.detail.mosaicTileSize;
        }
      });

      // Re-init Lucide icons after view switch
      this.$watch('viewMode', () => {
        this.$nextTick(() => {
          if (typeof lucide !== 'undefined') {
            lucide.createIcons();
          }
        });
      });
    },

    // Tile size computed helpers
    tileMinWidth() {
      return { 1: 100, 2: 140, 3: 180, 4: 230, 5: 290 }[this.mosaicTileSize] || 180;
    },
    tileGap() {
      return this.mosaicTileSize <= 2 ? 8 : 16;
    },
    tileIconSize() {
      return { 1: 28, 2: 36, 3: 48, 4: 64, 5: 80 }[this.mosaicTileSize] || 48;
    },

    setViewMode(mode) {
      this.viewMode = mode;
      window._filePrefsCache.defaultViewMode = mode;
      this._saveFilePrefs();
    },

    setMosaicTileSize(size) {
      window._filePrefsCache.mosaicTileSize = parseInt(size);
      this._saveFilePrefs();
    },

    _saveFilePrefs() {
      const API_URL = '/api/v1/settings/files/preferences';
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
        || '';

      fetch(API_URL, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ value: { ...window._filePrefsCache } }),
        credentials: 'same-origin',
      }).catch(() => {});
    },

    navigateToFolder(event, url) {
      // Only navigate if not clicking checkbox or action button
      if (event.target.closest('input[type="checkbox"]') ||
          event.target.closest('button') ||
          event.target.closest('[data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        const card = event.currentTarget;
        const uuid = card?.dataset?.uuid;
        if (uuid) {
          this.toggleRowSelection(uuid, event.shiftKey);
        }
        return;
      }
      // Normal behavior: navigate to folder
      const link = document.querySelector('#folder-nav-push');
      if (link) {
        link.href = url;
        link.click();
      }
    },

    openFileFromCard(event, uuid, name, mimeType) {
      // Only open if not clicking checkbox or action button
      if (event.target.closest('input[type="checkbox"]') ||
          event.target.closest('button') ||
          event.target.closest('[data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        this.toggleRowSelection(uuid, event.shiftKey);
        return;
      }
      // Normal behavior: open file viewer
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: { uuid, name, mime_type: mimeType }
      }));
    }
  };
};

// ── File Comments ──────────────────────────────────────────

window.fileComments = function fileComments(fileUuid, currentUserId) {
  return {
    fileUuid,
    currentUserId,
    comments: [],
    loading: true,
    newBody: '',
    sending: false,
    editingId: null,
    editBody: '',

    _csrf() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
        || '';
    },

    async init() {
      await this.loadComments();
    },

    async loadComments() {
      this.loading = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.comments = await resp.json();
        }
      } catch (e) { /* ignore */ }
      this.loading = false;
      this.$nextTick(() => {
        if (this.$refs.commentsList) {
          lucide.createIcons({ nodes: this.$refs.commentsList.querySelectorAll('[data-lucide]') });
        }
      });
    },

    async addComment() {
      if (!this.newBody.trim() || this.sending) return;
      this.sending = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
          body: JSON.stringify({ body: this.newBody.trim() }),
        });
        if (resp.ok) {
          this.newBody = '';
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
      this.sending = false;
    },

    _refreshIcons() {
      this.$nextTick(() => {
        if (this.$refs.commentsList) {
          lucide.createIcons({ nodes: this.$refs.commentsList.querySelectorAll('[data-lucide]') });
        }
      });
    },

    startEdit(comment) {
      this.editingId = comment.uuid;
      this.editBody = comment.body;
      this._refreshIcons();
    },

    cancelEdit() {
      this.editingId = null;
      this.editBody = '';
      this._refreshIcons();
    },

    async saveEdit(commentUuid) {
      if (!this.editBody.trim()) return;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments/${commentUuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
          body: JSON.stringify({ body: this.editBody.trim() }),
        });
        if (resp.ok) {
          this.editingId = null;
          this.editBody = '';
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
    },

    async deleteComment(commentUuid) {
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments/${commentUuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
        });
        if (resp.ok) {
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
    },

    formatDate(iso) {
      const d = new Date(iso);
      const now = new Date();
      const diff = now - d;
      if (diff < 60000) return 'just now';
      if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
      if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
      if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
      return d.toLocaleDateString();
    },
  };
};
